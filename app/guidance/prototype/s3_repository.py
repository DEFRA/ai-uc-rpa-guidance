"""S3 access for prototype guide artefacts synced by hand into S3.

Unlike app/guidance/documents, which is written via the CDP-uploader pipeline
and read/written through GuidanceS3Repository, this data is produced entirely
outside this service (by scripts/parse_docx_for_s3.py in the
rpa-ai-guidance-hub-api repo) and pushed with a manual `aws s3 sync`. This
repository writes guides only by streaming them out of an uploaded zip, and can
purge the whole prefix. It lives under its own key prefix
(prototype_guides/) so it can never collide with parsed_guidance/.
"""

import asyncio
import logging
import mimetypes
from typing import IO, Any

from app.guidance.prototype import s3_range_reader

logger = logging.getLogger(__name__)

PROTOTYPE_PREFIX = "prototype_guides"
MANIFEST_KEY = f"{PROTOTYPE_PREFIX}/manifest.json"

# The most keys S3's DeleteObjects accepts in one request.
_DELETE_BATCH_SIZE = 1000


class PurgeIncompleteError(RuntimeError):
    """Raised when S3 reports that some prototype guide objects were not deleted."""

    def __init__(self, failed: list[str], total: int) -> None:
        """Record which keys S3 failed to delete.

        Args:
            failed: The keys S3 reported errors for.
            total: How many keys the purge tried to delete.
        """
        super().__init__(
            f"{len(failed)} of {total} prototype guide objects could not be deleted"
        )
        self.failed = failed
        self.total = total


class PrototypeGuideS3Repository:
    """Repository for prototype guide artefacts stored in S3."""

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

    async def open_upload(self, bucket: str, key: str) -> IO[bytes]:
        """Open a file CDP uploader delivered, read by byte range as needed.

        Args:
            bucket: The bucket CDP uploader put the file in.
            key: The key CDP uploader put the file at.

        Returns:
            A seekable binary reader over the file; reads block, so use it
            from a worker thread.

        Raises:
            OSError: If the file cannot be looked up.
        """
        return await asyncio.to_thread(
            s3_range_reader.open_object, self.s3, bucket, key
        )

    async def delete_upload(self, bucket: str, key: str) -> None:
        """Delete a file CDP uploader delivered, once it has been dealt with.

        Args:
            bucket: The bucket CDP uploader put the file in.
            key: The key CDP uploader put the file at.
        """
        await asyncio.to_thread(self.s3.delete_object, Bucket=bucket, Key=key)

        logger.info("Deleted uploaded file s3://%s/%s", bucket, key)

    async def upload_stream(self, name: str, stream: Any) -> None:
        """Stream one file to prototype_guides/, overwriting any already there.

        Args:
            name: Its path relative to prototype_guides/.
            stream: A readable binary stream of its contents.
        """
        await asyncio.to_thread(
            self.s3.upload_fileobj,
            Fileobj=stream,
            Bucket=self.bucket,
            Key=f"{PROTOTYPE_PREFIX}/{name}",
            ExtraArgs={"ContentType": _content_type(name)},
        )

    async def purge(self) -> int:
        """Delete every object under prototype_guides/, the manifest included.

        Irreversible. Nothing outside the prefix is touched, so the
        CDP-uploader pipeline's parsed_guidance/ is safe.

        Returns:
            The number of objects deleted.

        Raises:
            PurgeIncompleteError: If S3 reports any object as not deleted.
                Every batch is still attempted first.
        """
        return await asyncio.to_thread(self._purge)

    def _purge(self) -> int:
        paginator = self.s3.get_paginator("list_objects_v2")
        keys = [
            obj["Key"]
            for page in paginator.paginate(
                Bucket=self.bucket, Prefix=f"{PROTOTYPE_PREFIX}/"
            )
            for obj in page.get("Contents", [])
        ]

        # DeleteObjects succeeds as a whole even when it fails to delete some
        # keys, listing those under Errors instead of raising.
        failed: list[str] = []
        for start in range(0, len(keys), _DELETE_BATCH_SIZE):
            batch = keys[start : start + _DELETE_BATCH_SIZE]
            response = self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            failed.extend(error["Key"] for error in response.get("Errors", []))

        if failed:
            logger.error(
                "Purge of s3://%s/%s/ left %d of %d objects, e.g. %s",
                self.bucket,
                PROTOTYPE_PREFIX,
                len(failed),
                len(keys),
                failed[:5],
            )
            raise PurgeIncompleteError(failed, len(keys))

        logger.info(
            "Purged %d prototype guide objects from s3://%s/%s/",
            len(keys),
            self.bucket,
            PROTOTYPE_PREFIX,
        )

        return len(keys)


def _content_type(name: str) -> str:
    if name.endswith(".md"):
        return "text/markdown; charset=utf-8"

    content_type, _ = mimetypes.guess_type(name)

    return content_type or "application/octet-stream"
