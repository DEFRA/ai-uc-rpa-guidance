"""Domain models for publishing analysis jobs."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    """Lifecycle status of a publishing analysis job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class PublishingJob:
    """A single publishing analysis job record."""

    id: uuid.UUID
    document_id: uuid.UUID
    status: JobStatus = JobStatus.PENDING
    result: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def to_document(self) -> dict[str, Any]:
        """Serialise the job to a MongoDB document dict."""
        return {
            "_id": self.id,
            "document_id": self.document_id,
            "status": self.status.value,
            "result": self.result,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> PublishingJob:
        """Map a MongoDB document dict to a PublishingJob."""
        return cls(
            id=doc["_id"],
            document_id=doc["document_id"],
            status=JobStatus(doc["status"]),
            result=doc.get("result"),
            error_message=doc.get("error_message"),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )


@dataclass
class AnalysisJob:
    """Payload passed to the job submitter for execution."""

    job_id: uuid.UUID
    document_id: uuid.UUID
    document_text: str


class JobNotFoundError(Exception):
    """Raised when a publishing job is not found."""
