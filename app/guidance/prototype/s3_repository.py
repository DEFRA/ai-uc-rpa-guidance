"""S3 access for prototype guide artefacts synced by hand into S3.

Unlike app/guidance/documents, which is written via the CDP-uploader pipeline
and read/written through GuidanceS3Repository, this data is produced entirely
outside this service (by scripts/parse_docx_for_s3.py in the
rpa-ai-guidance-hub-api repo) and pushed with a manual `aws s3 sync`. This
repository is therefore read-only and lives under its own key prefix
(prototype_guides/) so it can never collide with parsed_guidance/.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

PROTOTYPE_PREFIX = "prototype_guides"
MANIFEST_KEY = f"{PROTOTYPE_PREFIX}/manifest.json"


class PrototypeGuideS3Repository:
    """Read-only repository for prototype guide artefacts stored in S3."""

    def __init__(self, s3_client: Any, bucket: str) -> None:
        """Initialise with a boto3 S3 client and the bucket name.

        Args:
            s3_client: A boto3 S3 client.
            bucket: The S3 bucket holding the prototype_guides/ prefix. This
                is the same bucket the CDP-uploader pipeline writes
                parsed_guidance/ into.
        """
        self.s3 = s3_client
        self.bucket = bucket

    async def download_manifest(self) -> bytes:
        """Download prototype_guides/manifest.json.

        Returns:
            The raw manifest JSON bytes.
        """
        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=MANIFEST_KEY
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug(
            "Downloaded prototype manifest from s3://%s/%s", self.bucket, MANIFEST_KEY
        )

        return body

    async def download_content(self, document_id: str, version_id: str) -> bytes:
        """Download prototype_guides/{document_id}/{version_id}/content.md.

        Args:
            document_id: The guide's document id.
            version_id: The specific version id to read.

        Returns:
            The raw Markdown bytes for that version.
        """
        key = f"{PROTOTYPE_PREFIX}/{document_id}/{version_id}/content.md"

        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug("Downloaded prototype content from s3://%s/%s", self.bucket, key)

        return body

    async def download_asset(self, document_id: str, asset_id: str) -> bytes:
        """Download prototype_guides/{document_id}/assets/{asset_id}.

        Assets sit above versions and are content-addressed by digest, so a
        single asset is shared by every version of a guide.

        Args:
            document_id: The guide's document id.
            asset_id: The asset filename (e.g. a content-addressed digest
                with extension).

        Returns:
            The raw asset bytes.
        """
        key = f"{PROTOTYPE_PREFIX}/{document_id}/assets/{asset_id}"

        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug("Downloaded prototype asset from s3://%s/%s", self.bucket, key)

        return body
