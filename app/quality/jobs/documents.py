"""Guide content port and adapter for the quality jobs domain.

Quality checks read the prototype guides: the latest version of a guide, as
the prototype guides manifest names it, cut into its top-level parts.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Protocol

import botocore.exceptions

from app.guidance.prototype import manifest, s3_repository
from app.quality import anchors


@dataclass
class GuideContent:
    """The latest version of a prototype guide, ready to check."""

    document_id: uuid.UUID
    version_id: str
    title: str
    parts: list[anchors.Part]


class GuideContentSource(Protocol):
    """Port: provides prototype guide content to the quality jobs domain."""

    async def get(self, document_id: uuid.UUID) -> GuideContent | None:
        """Return the guide's latest version, or None if there is no such guide.

        Args:
            document_id: The guide's document id.

        Returns:
            The guide's content, or None.
        """
        ...


class PrototypeGuideContentSource:
    """Adapter that reads prototype guides from their S3 prefix and manifest."""

    def __init__(self, s3_repo: s3_repository.PrototypeGuideS3Repository) -> None:
        """Initialise with the prototype guide S3 repository.

        Args:
            s3_repo: The prototype guide S3 repository.
        """
        self._s3_repo = s3_repo

    async def get(self, document_id: uuid.UUID) -> GuideContent | None:
        """Return the guide's latest version, cut into its top-level parts.

        Args:
            document_id: The guide's document id.

        Returns:
            The guide's content, or None if no manifest has been uploaded or
            the guide is not in it.
        """
        try:
            raw_manifest = await self._s3_repo.download_manifest()
        except botocore.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

        latest = next(
            (
                version
                for version in manifest.latest_versions(json.loads(raw_manifest))
                if version.document_id == str(document_id)
            ),
            None,
        )
        if latest is None:
            return None

        content = await self._s3_repo.download_content(
            latest.document_id, latest.version_id
        )

        return GuideContent(
            document_id=document_id,
            version_id=latest.version_id,
            title=latest.title,
            parts=anchors.top_level_parts(content.decode()),
        )
