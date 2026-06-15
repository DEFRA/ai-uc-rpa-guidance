"""API request/response schemas for publishing analysis jobs."""

import uuid
from datetime import datetime

import pydantic
import pydantic.alias_generators

from app.publishing import api_schemas as publishing_schemas
from app.publishing.jobs import models


class AnalyseRequest(pydantic.BaseModel):
    """Request body for the async analysis endpoint."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    document_id: uuid.UUID = pydantic.Field(
        ...,
        description="ID of the guidance document to analyse",
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
    )


class AnalysisJobResponse(pydantic.BaseModel):
    """Response describing the state of a publishing analysis job."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    job_id: uuid.UUID = pydantic.Field(
        ..., description="Unique identifier for the analysis job"
    )
    document_id: uuid.UUID = pydantic.Field(
        ..., description="ID of the guidance document being analysed"
    )
    status: models.JobStatus = pydantic.Field(
        ..., description="Current job status: pending, running, completed, or error"
    )
    error_message: str | None = pydantic.Field(
        default=None, description="Error detail when status is error"
    )
    result: publishing_schemas.AnalyseResponse | None = pydantic.Field(
        default=None,
        description="Full analysis result (populated only when status is completed)",
    )
    created_at: datetime = pydantic.Field(..., description="When the job was created")
    updated_at: datetime = pydantic.Field(
        ..., description="When the job was last updated"
    )

    @classmethod
    def from_job(cls, job: models.PublishingJob) -> AnalysisJobResponse:
        """Build a response from a PublishingJob domain model.

        Args:
            job: The publishing job to map.

        Returns:
            AnalysisJobResponse with result populated when the job is completed.
        """
        result: publishing_schemas.AnalyseResponse | None = None
        if job.result is not None:
            result = publishing_schemas.AnalyseResponse.model_validate(job.result)
        return cls(
            job_id=job.id,
            document_id=job.document_id,
            status=job.status,
            error_message=job.error_message,
            result=result,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
