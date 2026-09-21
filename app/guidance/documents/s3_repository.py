"""S3 repository for reading and writing guidance document artefacts."""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

MARKDOWN_CONTENT_TYPE = "text/markdown"
PARSED_PREFIX = "parsed_guidance"
SUMMARY_FILENAME = "summary.md"

# S3 accepts at most this many keys in one delete_objects call.
_DELETE_BATCH_SIZE = 1000


def summary_key(document_id: uuid.UUID) -> str:
    """Return the storage key holding a document's summary."""
    return f"{PARSED_PREFIX}/{document_id}/{SUMMARY_FILENAME}"


class AbstractGuidanceStorageRepository(ABC):
    """Abstract base class for guidance document storage repositories."""

    @abstractmethod
    async def download_docx(self, key: str) -> bytes:
        """Download the source .docx from storage."""

    @abstractmethod
    async def upload_content(self, document_id: uuid.UUID, markdown: str) -> None:
        """Upload rendered Markdown content to storage."""

    @abstractmethod
    async def download_content(self, document_id: uuid.UUID) -> str:
        """Download the rendered Markdown content for a document from storage."""

    @abstractmethod
    async def upload_manifest(self, document_id: uuid.UUID, json_str: str) -> None:
        """Upload the document manifest JSON to storage."""

    @abstractmethod
    async def download_manifest(self, document_id: uuid.UUID) -> str:
        """Download the document manifest JSON from storage."""

    @abstractmethod
    async def upload_summary(self, document_id: uuid.UUID, markdown: str) -> str:
        """Upload the document's rendered summary Markdown, returning its path."""

    @abstractmethod
    async def delete_summaries(self) -> int:
        """Delete every stored summary, returning how many were removed."""

    @abstractmethod
    async def upload_section(
        self, document_id: uuid.UUID, section_number: str, markdown: str
    ) -> None:
        """Upload a single section's Markdown content to storage."""

    @abstractmethod
    async def download_section(
        self, document_id: uuid.UUID, section_number: str
    ) -> str:
        """Download a single section's Markdown content from storage."""

    @abstractmethod
    async def upload_image(
        self,
        document_id: uuid.UUID,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> None:
        """Upload an extracted image to storage."""

    @abstractmethod
    async def download_image(self, document_id: uuid.UUID, filename: str) -> bytes:
        """Download an extracted image from storage."""


class GuidanceS3Repository(AbstractGuidanceStorageRepository):
    """Repository for guidance document artefacts stored in S3."""

    def __init__(self, s3_client: Any, bucket: str) -> None:
        """Initialise with a boto3 S3 client and the output bucket name.

        Args:
            s3_client: A boto3 S3 client.
            bucket: The S3 bucket used for Markdown and image outputs.
        """
        self.s3 = s3_client
        self.bucket = bucket

    async def download_docx(self, key: str) -> bytes:
        """Download the source .docx from S3.

        Args:
            key: The S3 key of the document (e.g. folder/file.docx).

        Returns:
            Raw .docx bytes.
        """

        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug(
            "Downloaded docx from s3://%s/%s (%d bytes)", self.bucket, key, len(body)
        )

        return body

    async def upload_content(self, document_id: uuid.UUID, markdown: str) -> None:
        """Upload rendered Markdown to parsed_guidance/{document_id}/content.md.

        Args:
            document_id: The guidance document ID (used as the S3 key prefix).
            markdown: The rendered Markdown string.
        """
        key = f"parsed_guidance/{document_id}/content.md"

        await asyncio.to_thread(
            self.s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=markdown.encode(),
            ContentType=MARKDOWN_CONTENT_TYPE,
        )

        logger.debug("Uploaded markdown to s3://%s/%s", self.bucket, key)

    async def download_content(self, document_id: uuid.UUID) -> str:
        """Download the rendered Markdown for parsed_guidance/{document_id}/content.md.

        Args:
            document_id: The guidance document ID.

        Returns:
            The rendered Markdown string.
        """
        key = f"parsed_guidance/{document_id}/content.md"

        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug("Downloaded content from s3://%s/%s", self.bucket, key)

        return body.decode()

    async def upload_manifest(self, document_id: uuid.UUID, json_str: str) -> None:
        """Upload the manifest JSON to parsed_guidance/{document_id}/manifest.json.

        Args:
            document_id: The guidance document ID.
            json_str: The manifest serialised as a JSON string.
        """
        key = f"parsed_guidance/{document_id}/manifest.json"

        await asyncio.to_thread(
            self.s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=json_str.encode(),
            ContentType="application/json",
        )

        logger.debug("Uploaded manifest to s3://%s/%s", self.bucket, key)

    async def download_manifest(self, document_id: uuid.UUID) -> str:
        """Download the manifest JSON from parsed_guidance/{document_id}/manifest.json.

        Args:
            document_id: The guidance document ID.

        Returns:
            The manifest as a JSON string.
        """
        key = f"parsed_guidance/{document_id}/manifest.json"

        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug("Downloaded manifest from s3://%s/%s", self.bucket, key)

        return body.decode()

    async def upload_summary(self, document_id: uuid.UUID, markdown: str) -> str:
        """Upload the summary to parsed_guidance/{document_id}/summary.md.

        The summary is written beside the parse outputs so that anything
        reading a document's prefix finds it there. Nothing in this service
        reads it back — the queryable copy lives in Mongo — but the search
        index this feeds will be built from the document prefix.

        Args:
            document_id: The guidance document ID.
            markdown: The rendered summary Markdown.

        Returns:
            The storage path written, as s3://bucket/key.
        """
        key = summary_key(document_id)

        await asyncio.to_thread(
            self.s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=markdown.encode(),
            ContentType=MARKDOWN_CONTENT_TYPE,
        )

        logger.debug("Uploaded summary to s3://%s/%s", self.bucket, key)

        return f"s3://{self.bucket}/{key}"

    async def delete_summaries(self) -> int:
        """Delete every summary under the parsed-guidance prefix.

        The bucket is listed rather than the stored summary records, so a
        summary whose record was lost is removed too: after this, no summary
        object survives that the rebuild did not write.

        Returns:
            How many summary objects were deleted.
        """
        keys = await asyncio.to_thread(self._list_summary_keys)

        for start in range(0, len(keys), _DELETE_BATCH_SIZE):
            batch = keys[start : start + _DELETE_BATCH_SIZE]
            await asyncio.to_thread(
                self.s3.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch]},
            )

        logger.info("Deleted %d summary object(s) from %s", len(keys), self.bucket)

        return len(keys)

    def _list_summary_keys(self) -> list[str]:
        """Return the key of every summary object in the bucket."""
        paginator = self.s3.get_paginator("list_objects_v2")

        return [
            item["Key"]
            for page in paginator.paginate(
                Bucket=self.bucket, Prefix=f"{PARSED_PREFIX}/"
            )
            for item in page.get("Contents", [])
            if item["Key"].endswith(f"/{SUMMARY_FILENAME}")
        ]

    async def upload_section(
        self, document_id: uuid.UUID, section_number: str, markdown: str
    ) -> None:
        """Upload a section's Markdown to parsed_guidance/{document_id}/sections/{number}.md.

        Args:
            document_id: The guidance document ID.
            section_number: The hierarchical section number (e.g. "1.2.3").
            markdown: The rendered Markdown for this section's direct content.
        """
        key = f"parsed_guidance/{document_id}/sections/{section_number}.md"

        await asyncio.to_thread(
            self.s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=markdown.encode(),
            ContentType=MARKDOWN_CONTENT_TYPE,
        )

        logger.debug(
            "Uploaded section %s to s3://%s/%s", section_number, self.bucket, key
        )

    async def download_section(
        self, document_id: uuid.UUID, section_number: str
    ) -> str:
        """Download a section's Markdown from parsed_guidance/{document_id}/sections/{number}.md.

        Args:
            document_id: The guidance document ID.
            section_number: The hierarchical section number (e.g. "1.2.3").

        Returns:
            The rendered Markdown for this section's direct content.
        """
        key = f"parsed_guidance/{document_id}/sections/{section_number}.md"

        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug(
            "Downloaded section %s from s3://%s/%s", section_number, self.bucket, key
        )

        return body.decode()

    async def upload_image(
        self,
        document_id: uuid.UUID,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> None:
        """Upload an extracted image to parsed_guidance/{document_id}/images/{filename}.

        Args:
            document_id: The guidance document ID.
            filename: The image filename (e.g. "img_1.png").
            data: Raw image bytes.
            content_type: MIME type (e.g. "image/png").
        """
        key = f"parsed_guidance/{document_id}/images/{filename}"

        await asyncio.to_thread(
            self.s3.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

        logger.debug("Uploaded image to s3://%s/%s", self.bucket, key)

    async def download_image(self, document_id: uuid.UUID, filename: str) -> bytes:
        """Download an extracted image from parsed_guidance/{document_id}/images/{filename}.

        Args:
            document_id: The guidance document ID.
            filename: The image filename (e.g. "img_1.png").

        Returns:
            Raw image bytes.
        """
        key = f"parsed_guidance/{document_id}/images/{filename}"

        response = await asyncio.to_thread(
            self.s3.get_object, Bucket=self.bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)

        logger.debug("Downloaded image from s3://%s/%s", self.bucket, key)

        return body
