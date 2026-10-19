"""API request/response schemas for quality check jobs."""

import uuid
from datetime import datetime

import pydantic
import pydantic.alias_generators

from app.quality import api_schemas as quality_schemas
from app.quality.jobs import models


class StartCheckRequest(pydantic.BaseModel):
    """Request body for starting a quality check."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    document_id: uuid.UUID = pydantic.Field(
        ...,
        description="ID of the prototype guide to check",
        examples=["48b8a1df-c71b-4de3-ac73-7c672aaf5456"],
    )


class QualityJobResponse(pydantic.BaseModel):
    """Response describing the state of a quality check job."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    job_id: uuid.UUID = pydantic.Field(..., description="The quality check job's id")
    document_id: uuid.UUID = pydantic.Field(
        ..., description="ID of the prototype guide checked"
    )
    version_id: str = pydantic.Field(
        ..., description="The version of the guide the comments are anchored in"
    )
    status: models.JobStatus = pydantic.Field(
        ..., description="Current job status: pending, running, completed, or error"
    )
    error_message: str | None = pydantic.Field(
        default=None, description="Error detail when status is error"
    )
    sections_total: int = pydantic.Field(
        ..., description="How many top-level sections the guide has"
    )
    sections_done: int = pydantic.Field(
        ..., description="How many of them have been checked so far"
    )
    result: quality_schemas.QualityCheckResult | None = pydantic.Field(
        default=None,
        description="The comments (populated only when status is completed)",
    )
    created_at: datetime = pydantic.Field(..., description="When the job was created")
    updated_at: datetime = pydantic.Field(
        ..., description="When the job was last updated"
    )

    @classmethod
    def from_job(cls, job: models.QualityJob) -> QualityJobResponse:
        """Build a response from a QualityJob domain model.

        Args:
            job: The quality job to map.

        Returns:
            QualityJobResponse with the result populated when the job completed.
        """
        result: quality_schemas.QualityCheckResult | None = None
        if job.result is not None:
            result = quality_schemas.QualityCheckResult.model_validate(job.result)
        return cls(
            job_id=job.id,
            document_id=job.document_id,
            version_id=job.version_id,
            status=job.status,
            error_message=job.error_message,
            sections_total=job.sections_total,
            sections_done=job.sections_done,
            result=result,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
