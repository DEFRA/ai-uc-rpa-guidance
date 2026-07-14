"""FastAPI router for the feedback domain."""

import logging
import uuid
from typing import Annotated

import fastapi

from app.feedback import api_schemas, dependencies, models, service

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/feedback", tags=["feedback"])


@router.post(
    "",
    status_code=fastapi.status.HTTP_201_CREATED,
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Job or finding not found",
        },
        fastapi.status.HTTP_409_CONFLICT: {
            "description": "Feedback already exists for this job+finding",
        },
        fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "job_id exists but was produced by a different agent",
        },
    },
)
async def create_feedback(
    request: api_schemas.CreateFeedbackRequest,
    feedback_service: Annotated[
        service.FeedbackService,
        fastapi.Depends(dependencies.get_feedback_service),
    ],
) -> api_schemas.FeedbackResponse:
    """Submit feedback for a finding or for a job as a whole.

    Args:
        request: The feedback request.
        feedback_service: The feedback service, injected via FastAPI DI.

    Returns:
        The created feedback entry.

    Raises:
        HTTPException: 404 if the job or finding does not exist.
        HTTPException: 409 if feedback already exists for this job+finding.
    """
    try:
        entry = await feedback_service.create_feedback(
            job_id=request.job_id,
            agent=request.agent,
            finding_index=request.finding_index,
            verdict=request.verdict,
            comment=request.comment,
        )
    except service.JobNotFoundError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job {request.job_id} not found or not completed",
        ) from None
    except service.FindingNotFoundError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Finding {request.finding_index} not found in job {request.job_id}",
        ) from None
    except service.FeedbackAlreadyExistsError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail=f"Feedback already exists for job {request.job_id} finding {request.finding_index}",
        ) from None
    except service.AgentJobMismatchError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Job {request.job_id} was not produced by agent {request.agent}",
        ) from None

    return api_schemas.FeedbackResponse.from_entry(entry)


@router.get(
    "/jobs/{job_id}",
    responses={},
)
async def get_feedback_for_job(
    job_id: uuid.UUID,
    feedback_service: Annotated[
        service.FeedbackService,
        fastapi.Depends(dependencies.get_feedback_service),
    ],
) -> list[api_schemas.FeedbackResponse]:
    """Retrieve all feedback for a job.

    Args:
        job_id: The job UUID path parameter.
        feedback_service: The feedback service, injected via FastAPI DI.

    Returns:
        List of feedback entries (empty if none exist).
    """
    entries = await feedback_service.get_feedback_for_job(job_id)
    return [api_schemas.FeedbackResponse.from_entry(e) for e in entries]


@router.get(
    "/jobs/{job_id}/findings/{finding_index}",
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "No feedback for this finding",
        },
    },
)
async def get_feedback_for_finding(
    job_id: uuid.UUID,
    finding_index: int,
    feedback_service: Annotated[
        service.FeedbackService,
        fastapi.Depends(dependencies.get_feedback_service),
    ],
) -> api_schemas.FeedbackResponse:
    """Retrieve feedback for a specific finding.

    Args:
        job_id: The job UUID path parameter.
        finding_index: The 0-based finding index path parameter.
        feedback_service: The feedback service, injected via FastAPI DI.

    Returns:
        The feedback entry for this finding.

    Raises:
        HTTPException: 404 if no feedback exists for this finding.
    """
    entry = await feedback_service.get_feedback_for_finding(job_id, finding_index)

    if entry is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No feedback for job {job_id} finding {finding_index}",
        )

    return api_schemas.FeedbackResponse.from_entry(entry)


@router.get(
    "/{feedback_id}",
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Feedback not found",
        },
    },
)
async def get_feedback(
    feedback_id: uuid.UUID,
    feedback_service: Annotated[
        service.FeedbackService,
        fastapi.Depends(dependencies.get_feedback_service),
    ],
) -> api_schemas.FeedbackResponse:
    """Retrieve a feedback entry by ID.

    Args:
        feedback_id: The feedback UUID path parameter.
        feedback_service: The feedback service, injected via FastAPI DI.

    Returns:
        The feedback entry.

    Raises:
        HTTPException: 404 if the feedback entry does not exist.
    """
    entry = await feedback_service.get_feedback(feedback_id)

    if entry is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Feedback {feedback_id} not found",
        )

    return api_schemas.FeedbackResponse.from_entry(entry)


@router.put(
    "/{feedback_id}",
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Feedback not found",
        },
    },
)
async def update_feedback(
    feedback_id: uuid.UUID,
    request: api_schemas.UpdateFeedbackRequest,
    feedback_service: Annotated[
        service.FeedbackService,
        fastapi.Depends(dependencies.get_feedback_service),
    ],
) -> api_schemas.FeedbackResponse:
    """Update the verdict and/or comment on a feedback entry.

    Args:
        feedback_id: The feedback UUID path parameter.
        request: Fields to update.
        feedback_service: The feedback service, injected via FastAPI DI.

    Returns:
        The updated feedback entry.

    Raises:
        HTTPException: 404 if the feedback entry does not exist.
    """
    try:
        entry = await feedback_service.update_feedback(
            feedback_id=feedback_id,
            verdict=request.verdict,
            comment=request.comment,
        )
    except models.FeedbackNotFoundError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Feedback {feedback_id} not found",
        ) from None

    return api_schemas.FeedbackResponse.from_entry(entry)
