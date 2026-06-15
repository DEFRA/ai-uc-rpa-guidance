"""FastAPI router for publishing analysis job endpoints."""

import logging
import uuid
from typing import Annotated

import fastapi

from app.publishing.jobs import api_schemas, dependencies, service

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(tags=["publishing"])


@router.post(
    "/analyse",
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Document not found",
        },
        fastapi.status.HTTP_409_CONFLICT: {
            "description": "Document has not been fully parsed yet",
        },
    },
)
async def analyse_document(
    request: api_schemas.AnalyseRequest,
    job_service: Annotated[
        service.PublishingJobService,
        fastapi.Depends(dependencies.get_publishing_job_service),
    ],
) -> api_schemas.AnalysisJobResponse:
    """Submit a guidance document for async QA analysis.

    Accepts a document ID, creates a job record, and enqueues the analysis.
    Returns 202 immediately; poll GET /publishing/jobs/{jobId} for results.

    Args:
        request: The analysis request containing the document ID.
        job_service: The publishing job service, injected via FastAPI DI.

    Returns:
        The created job with status pending.

    Raises:
        HTTPException: 404 if the document does not exist.
        HTTPException: 409 if the document has not been fully parsed.
    """
    try:
        job = await job_service.start_analysis(request.document_id)
    except service.DocumentNotFoundError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Document {request.document_id} not found",
        ) from None
    except service.DocumentNotReadyError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail=f"Document {request.document_id} has not been fully parsed",
        ) from None

    return api_schemas.AnalysisJobResponse.from_job(job)


@router.get(
    "/jobs/{job_id}",
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Job not found",
        },
    },
)
async def get_job(
    job_id: uuid.UUID,
    job_service: Annotated[
        service.PublishingJobService,
        fastapi.Depends(dependencies.get_publishing_job_service),
    ],
) -> api_schemas.AnalysisJobResponse:
    """Retrieve a publishing analysis job by ID.

    Args:
        job_id: The job UUID path parameter.
        job_service: The publishing job service, injected via FastAPI DI.

    Returns:
        The job record with current status and result when completed.

    Raises:
        HTTPException: 404 if the job does not exist.
    """
    job = await job_service.get_job(job_id)

    if not job:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    return api_schemas.AnalysisJobResponse.from_job(job)


@router.get(
    "/documents/{document_id}/analysis",
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "No analysis run found for this document",
        },
    },
)
async def get_latest_analysis(
    document_id: uuid.UUID,
    job_service: Annotated[
        service.PublishingJobService,
        fastapi.Depends(dependencies.get_publishing_job_service),
    ],
) -> api_schemas.AnalysisJobResponse:
    """Retrieve the latest analysis job for a guidance document.

    Args:
        document_id: The guidance document UUID path parameter.
        job_service: The publishing job service, injected via FastAPI DI.

    Returns:
        The most recent analysis job for the document.

    Raises:
        HTTPException: 404 if no analysis jobs exist for the document.
    """
    job = await job_service.get_latest_for_document(document_id)

    if not job:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No analysis found for document {document_id}",
        )

    return api_schemas.AnalysisJobResponse.from_job(job)
