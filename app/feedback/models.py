"""Domain models for the feedback domain."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class FeedbackVerdict(StrEnum):
    """The user's verdict on a finding."""

    FIX = "fix"
    WONT_FIX = "wont_fix"
    FALSE_POSITIVE = "false_positive"


class AgentName(StrEnum):
    """The agent that produced the finding being reviewed."""

    CHECKER = "checker"
    CRITIC = "critic"


@dataclass
class FindingSnapshot:
    """Point-in-time capture of a finding's content at feedback submission time."""

    agent: AgentName
    fields: dict[str, Any]


@dataclass
class FeedbackEntry:
    """A user verdict on a specific finding or on a job as a whole."""

    id: uuid.UUID
    job_id: uuid.UUID
    agent: AgentName
    finding_index: int | None
    verdict: FeedbackVerdict
    comment: str | None = None
    finding_snapshot: FindingSnapshot | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def to_document(self) -> dict[str, Any]:
        """Serialise to a MongoDB document dict."""
        snapshot: dict[str, Any] | None = None
        if self.finding_snapshot is not None:
            snapshot = {
                "agent": self.finding_snapshot.agent.value,
                "fields": self.finding_snapshot.fields,
            }
        return {
            "_id": self.id,
            "job_id": self.job_id,
            "agent": self.agent.value,
            "finding_index": self.finding_index,
            "verdict": self.verdict.value,
            "comment": self.comment,
            "finding_snapshot": snapshot,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> FeedbackEntry:
        """Reconstruct a FeedbackEntry from a MongoDB document dict."""
        snapshot: FindingSnapshot | None = None
        raw_snapshot = doc.get("finding_snapshot")
        if raw_snapshot is not None:
            snapshot = FindingSnapshot(
                agent=AgentName(raw_snapshot["agent"]),
                fields=raw_snapshot["fields"],
            )
        return cls(
            id=doc["_id"],
            job_id=doc["job_id"],
            agent=AgentName(doc["agent"]),
            finding_index=doc.get("finding_index"),
            verdict=FeedbackVerdict(doc["verdict"]),
            comment=doc.get("comment"),
            finding_snapshot=snapshot,
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )


class FeedbackNotFoundError(Exception):
    """Raised when a feedback entry is not found."""
