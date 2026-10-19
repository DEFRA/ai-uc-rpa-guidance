"""Domain models for quality check jobs."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.quality import anchors


class JobStatus(StrEnum):
    """Lifecycle status of a quality check job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class QualityJob:
    """One quality check of one version of a prototype guide.

    Every run is kept. A comment's anchor only fits the Markdown it was made
    from, so the job records which version of the guide it checked.
    """

    id: uuid.UUID
    document_id: uuid.UUID
    version_id: str
    status: JobStatus = JobStatus.PENDING
    result: dict[str, Any] | None = None
    error_message: str | None = None
    # How far the check has got: top-level sections in the guide, and how
    # many of them have been checked.
    sections_total: int = 0
    sections_done: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def to_document(self) -> dict[str, Any]:
        """Serialise the job to a MongoDB document dict."""
        return {
            "_id": self.id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "status": self.status.value,
            "result": self.result,
            "error_message": self.error_message,
            "sections_total": self.sections_total,
            "sections_done": self.sections_done,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> QualityJob:
        """Map a MongoDB document dict to a QualityJob."""
        return cls(
            id=doc["_id"],
            document_id=doc["document_id"],
            version_id=doc["version_id"],
            status=JobStatus(doc["status"]),
            result=doc.get("result"),
            error_message=doc.get("error_message"),
            sections_total=doc.get("sections_total", 0),
            sections_done=doc.get("sections_done", 0),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )


@dataclass
class CheckJob:
    """Payload passed to the job submitter for execution."""

    job_id: uuid.UUID
    document_title: str
    parts: list[anchors.Part]
