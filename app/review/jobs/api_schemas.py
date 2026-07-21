"""API request/response schemas for guidance review jobs."""

import uuid
from datetime import datetime

import pydantic
import pydantic.alias_generators

from app.review import api_schemas as review_schemas
from app.review.jobs import models


class ReviewRequest(pydantic.BaseModel):
    """Request body for the async review endpoint."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    document_id: uuid.UUID = pydantic.Field(
        ...,
        description="ID of the guidance document to review",
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
    )


class ReviewJobResponse(pydantic.BaseModel):
    """Response describing the state of a guidance review job."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
    )

    job_id: uuid.UUID = pydantic.Field(
        ..., description="Unique identifier for the review job"
    )
    document_id: uuid.UUID = pydantic.Field(
        ..., description="ID of the guidance document being reviewed"
    )
    status: models.JobStatus = pydantic.Field(
        ..., description="Current job status: pending, running, completed, or error"
    )
    error_message: str | None = pydantic.Field(
        default=None, description="Error detail when status is error"
    )
    result: review_schemas.ReviewResponse | None = pydantic.Field(
        default=None,
        description="Full review result (populated only when status is completed)",
    )
    created_at: datetime = pydantic.Field(..., description="When the job was created")
    updated_at: datetime = pydantic.Field(
        ..., description="When the job was last updated"
    )

    @classmethod
    def from_job(cls, job: models.ReviewJob) -> ReviewJobResponse:
        """Build a response from a ReviewJob domain model.

        Args:
            job: The review job to map.

        Returns:
            ReviewJobResponse with result populated when the job is completed.
        """
        result: review_schemas.ReviewResponse | None = None
        if job.result is not None:
            result = review_schemas.ReviewResponse.model_validate(job.result)
        return cls(
            job_id=job.id,
            document_id=job.document_id,
            status=job.status,
            error_message=job.error_message,
            result=result,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
