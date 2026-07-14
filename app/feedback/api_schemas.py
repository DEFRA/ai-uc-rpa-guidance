"""API request/response schemas for the feedback domain."""

import uuid
from datetime import datetime
from typing import Any

import pydantic
import pydantic.alias_generators

from app.feedback import models


class CreateFeedbackRequest(pydantic.BaseModel):
    """Request body for creating a feedback entry."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    job_id: uuid.UUID = pydantic.Field(..., description="The job UUID")
    agent: models.AgentName = pydantic.Field(
        ..., description="The agent that produced the finding: checker or critic"
    )
    finding_index: int | None = pydantic.Field(
        default=None,
        description="0-based finding index, or null for job-level feedback",
    )
    verdict: models.FeedbackVerdict = pydantic.Field(
        ..., description="fix, wont_fix, or false_positive"
    )
    comment: str | None = pydantic.Field(
        default=None, description="Optional free-text comment"
    )


class UpdateFeedbackRequest(pydantic.BaseModel):
    """Request body for updating a feedback entry."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    verdict: models.FeedbackVerdict | None = pydantic.Field(
        default=None, description="New verdict, or omit to leave unchanged"
    )
    comment: str | None = pydantic.Field(
        default=None, description="New comment, or omit to leave unchanged"
    )


class FindingSnapshotResponse(pydantic.BaseModel):
    """Snapshot of the finding content captured at feedback submission time."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    agent: models.AgentName
    fields: dict[str, Any]


class FeedbackResponse(pydantic.BaseModel):
    """Response describing a feedback entry."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    id: uuid.UUID
    job_id: uuid.UUID
    agent: models.AgentName
    finding_index: int | None
    verdict: models.FeedbackVerdict
    comment: str | None
    finding_snapshot: FindingSnapshotResponse | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entry(cls, entry: models.FeedbackEntry) -> FeedbackResponse:
        """Build a response from a FeedbackEntry domain model.

        Args:
            entry: The feedback entry to map.

        Returns:
            FeedbackResponse with snapshot populated when present.
        """
        snapshot: FindingSnapshotResponse | None = None
        if entry.finding_snapshot is not None:
            snapshot = FindingSnapshotResponse(
                agent=entry.finding_snapshot.agent,
                fields=entry.finding_snapshot.fields,
            )
        return cls(
            id=entry.id,
            job_id=entry.job_id,
            agent=entry.agent,
            finding_index=entry.finding_index,
            verdict=entry.verdict,
            comment=entry.comment,
            finding_snapshot=snapshot,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )
