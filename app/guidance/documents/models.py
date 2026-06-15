"""Domain models for the guidance document management domain."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ExtractionStatus(StrEnum):
    """Processing status for guidance documents."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class GuidanceDocument:
    """Domain model for a guidance document."""

    id: uuid.UUID
    title: str | None = None
    description: str | None = None
    filename: str | None = None
    path: str | None = None
    status: ExtractionStatus = ExtractionStatus.PENDING
    content_hash: str | None = None
    content: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    error_message: str | None = None

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> GuidanceDocument:
        """Map a MongoDB document to a GuidanceDocument model.

        Args:
            doc: The MongoDB document dictionary.

        Returns:
            A GuidanceDocument model instance.
        """
        return cls(
            id=doc["_id"],
            title=doc.get("title"),
            description=doc.get("description"),
            filename=doc.get("filename"),
            path=doc.get("path"),
            status=ExtractionStatus(doc["status"]),
            content_hash=doc.get("content_hash"),
            content=doc.get("content"),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            error_message=doc.get("error_message"),
        )
