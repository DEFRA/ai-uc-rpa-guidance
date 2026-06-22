"""S3 repository for reading and writing guidance document artefacts."""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


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

        logger.info(
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
            ContentType="text/markdown",
        )

        logger.info("Uploaded markdown to s3://%s/%s", self.bucket, key)

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

        logger.info("Downloaded content from s3://%s/%s", self.bucket, key)

        return body.decode()
