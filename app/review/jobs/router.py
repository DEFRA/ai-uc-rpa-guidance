"""FastAPI router for guidance review job endpoints."""

import logging
import uuid
from typing import Annotated

import fastapi

from app.review.jobs import api_schemas, dependencies, service

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(tags=["review"])


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
async def review_document(
    request: api_schemas.ReviewRequest,
    job_service: Annotated[
        service.ReviewJobService,
        fastapi.Depends(dependencies.get_review_job_service),
    ],
) -> api_schemas.ReviewJobResponse:
    """Submit a guidance document for async review.

    Accepts a document ID, creates a job record, and enqueues the review.
    Returns 202 immediately; poll GET /review/jobs/{jobId} for results.

    Args:
        request: The review request containing the document ID.
        job_service: The review job service, injected via FastAPI DI.

    Returns:
        The created job with status pending.

    Raises:
        HTTPException: 404 if the document does not exist.
        HTTPException: 409 if the document has not been fully parsed.
    """
    try:
        job = await job_service.start_review(request.document_id)
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

    return api_schemas.ReviewJobResponse.from_job(job)


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
        service.ReviewJobService,
        fastapi.Depends(dependencies.get_review_job_service),
    ],
) -> api_schemas.ReviewJobResponse:
    """Retrieve a guidance review job by ID.

    Args:
        job_id: The job UUID path parameter.
        job_service: The review job service, injected via FastAPI DI.

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

    return api_schemas.ReviewJobResponse.from_job(job)


@router.get(
    "/documents/{document_id}/review",
    responses={
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "No review found for this document",
        },
    },
)
async def get_latest_review(
    document_id: uuid.UUID,
    job_service: Annotated[
        service.ReviewJobService,
        fastapi.Depends(dependencies.get_review_job_service),
    ],
) -> api_schemas.ReviewJobResponse:
    """Retrieve the latest review job for a guidance document.

    Args:
        document_id: The guidance document UUID path parameter.
        job_service: The review job service, injected via FastAPI DI.

    Returns:
        The most recent review job for the document.

    Raises:
        HTTPException: 404 if no review jobs exist for the document.
    """
    job = await job_service.get_latest_for_document(document_id)

    if not job:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No review found for document {document_id}",
        )

    return api_schemas.ReviewJobResponse.from_job(job)
