"""FastAPI router for quality check job endpoints."""

import logging
import uuid
from typing import Annotated

import fastapi

from app.quality.jobs import api_schemas, dependencies, service

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(tags=["quality-checks"])

_Service = Annotated[
    service.QualityJobService,
    fastapi.Depends(dependencies.get_quality_job_service),
]


@router.post(
    "/check",
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {"description": "Guide not found"},
    },
)
async def start_check(
    request: api_schemas.StartCheckRequest, job_service: _Service
) -> api_schemas.QualityJobResponse:
    """Start a quality check of a prototype guide's latest version.

    Returns 202 at once; poll GET /quality-checks/jobs/{jobId}, or get the
    guide's latest check from GET /quality-checks/documents/{documentId}/latest.

    Args:
        request: The request, holding the guide's document id.
        job_service: The quality job service, injected via FastAPI DI.

    Returns:
        The created job with status pending.

    Raises:
        HTTPException: 404 if the guide does not exist.
    """
    try:
        job = await job_service.start_check(request.document_id)
    except service.GuideNotFoundError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Guide {request.document_id} not found",
        ) from None

    return api_schemas.QualityJobResponse.from_job(job)


@router.get(
    "/jobs/{job_id}",
    responses={fastapi.status.HTTP_404_NOT_FOUND: {"description": "Job not found"}},
)
async def get_job(
    job_id: uuid.UUID, job_service: _Service
) -> api_schemas.QualityJobResponse:
    """Retrieve a quality check job by its id.

    Args:
        job_id: The job UUID path parameter.
        job_service: The quality job service, injected via FastAPI DI.

    Returns:
        The job, with its comments when it has completed.

    Raises:
        HTTPException: 404 if the job does not exist.
    """
    job = await job_service.get_job(job_id)

    if not job:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    return api_schemas.QualityJobResponse.from_job(job)


@router.get(
    "/documents/{document_id}/latest",
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "The guide has never been checked",
        },
    },
)
async def get_latest_check(
    document_id: uuid.UUID, job_service: _Service
) -> api_schemas.QualityJobResponse:
    """Retrieve the latest quality check of a prototype guide.

    Args:
        document_id: The guide's document id path parameter.
        job_service: The quality job service, injected via FastAPI DI.

    Returns:
        The most recent check of the guide, whatever its status.

    Raises:
        HTTPException: 404 if the guide has never been checked.
    """
    job = await job_service.get_latest_for_document(document_id)

    if not job:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No quality check found for guide {document_id}",
        )

    return api_schemas.QualityJobResponse.from_job(job)
