"""FastAPI router for the guidance document management endpoints."""

import logging
from typing import Annotated

import fastapi

from app.guidance.documents import api_schemas, dependencies, service

router = fastapi.APIRouter(prefix="/guidance/documents", tags=["documents"])

logger = logging.getLogger(__name__)


@router.post(
    "/",
    status_code=fastapi.status.HTTP_201_CREATED,
    responses={
        fastapi.status.HTTP_201_CREATED: {
            "description": "Upload session initiated successfully",
        },
        fastapi.status.HTTP_502_BAD_GATEWAY: {
            "description": "CDP uploader service is unavailable or returned an error",
        },
    },
)
async def initiate_document_upload(
    payload: api_schemas.DocumentUploadRequest,
    guidance_service: Annotated[
        service.GuidanceService,
        fastapi.Depends(dependencies.get_guidance_service),
    ],
) -> api_schemas.DocumentUploadResponse:
    """Initiate a document upload session.

    Args:
        payload: Upload configuration (title, description, redirect).
        guidance_service: The guidance service, injected via FastAPI DI.

    Returns:
        DocumentUploadResponse with the upload_id.

    Raises:
        HTTPException: If the CDP uploader service returns an error (502).
    """
    try:
        upload_id = await guidance_service.initiate_upload(payload)
    except Exception as e:
        logger.error("Failed to initiate upload: %s", str(e))
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="Failed to initiate upload with CDP uploader service",
        ) from e

    return api_schemas.DocumentUploadResponse(uploadId=upload_id)


@router.post(
    "/{document_id}/callback",
    status_code=fastapi.status.HTTP_204_NO_CONTENT,
    responses={
        fastapi.status.HTTP_204_NO_CONTENT: {
            "description": "Callback processed successfully",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Document not found for the given document_id",
        },
    },
)
async def handle_upload_callback(
    document_id: str,
    payload: api_schemas.CdpUploaderStatusPayload,
    guidance_service: Annotated[
        service.GuidanceService,
        fastapi.Depends(dependencies.get_guidance_service),
    ],
) -> None:
    """Handle callbacks from the CDP uploader service.

    Processes the uploaded file and triggers the document parser.

    Args:
        document_id: The document ID path parameter.
        payload: The callback payload from the uploader.
        guidance_service: The guidance service, injected via FastAPI DI.

    Raises:
        HTTPException: If the document is not found (404).
    """
    try:
        await guidance_service.handle_callback(
            document_id,
            payload,
        )
    except ValueError as e:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.get(
    "/",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Paginated list of guidance documents",
        },
    },
)
async def list_documents(
    guidance_service: Annotated[
        service.GuidanceService,
        fastapi.Depends(dependencies.get_guidance_service),
    ],
    page: int = fastapi.Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = fastapi.Query(
        10, ge=1, le=100, description="Number of items per page"
    ),
) -> api_schemas.DocumentListResponse:
    """Get a paginated list of all guidance documents.

    Args:
        guidance_service: The guidance service, injected via FastAPI DI.
        page: Page number (1-based).
        page_size: Number of items per page.

    Returns:
        Paginated list of documents with status and timestamps.
    """
    return await guidance_service.list_documents(page, page_size)
