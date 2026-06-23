"""FastAPI router for the guidance document management endpoints."""

import logging
import uuid
from typing import Annotated

import botocore.exceptions
import fastapi

from app.guidance.documents import api_schemas, dependencies, s3_repository, service

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
        logger.exception("Failed to initiate upload")
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
    document_id: uuid.UUID,
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
    page: Annotated[int, fastapi.Query(ge=1, description="Page number (1-based)")] = 1,
    page_size: Annotated[
        int, fastapi.Query(ge=1, le=100, description="Number of items per page")
    ] = 10,
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


@router.get(
    "/{document_id}/manifest",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Document section manifest",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Manifest not found — document may not have completed processing",
        },
    },
)
async def get_document_manifest(
    document_id: uuid.UUID,
    s3_repo: Annotated[
        s3_repository.AbstractGuidanceStorageRepository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
) -> api_schemas.DocumentManifestResponse:
    """Return the section graph manifest for a parsed guidance document.

    Args:
        document_id: The guidance document UUID.
        s3_repo: The S3 repository, injected via FastAPI DI.

    Returns:
        DocumentManifestResponse with a flat adjacency-list of all sections.

    Raises:
        HTTPException: 404 if the manifest has not been produced yet.
    """
    logger.info("Fetching manifest for document %s", document_id)
    try:
        raw = await s3_repo.download_manifest(document_id)
        return api_schemas.DocumentManifestResponse.model_validate_json(raw)
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Manifest not found",
            ) from exc
        raise


@router.get(
    "/{document_id}/sections/{section_number}",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Section Markdown content (direct content only, no children)",
            "content": {"text/markdown": {}},
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Section not found",
        },
    },
)
async def get_document_section(
    document_id: uuid.UUID,
    section_number: Annotated[
        str,
        fastapi.Path(
            pattern=r"^\d+(\.\d+)*$",
            description="Hierarchical section number, e.g. 1.2.3",
        ),
    ],
    s3_repo: Annotated[
        s3_repository.AbstractGuidanceStorageRepository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
) -> fastapi.Response:
    """Return the Markdown content for a single document section.

    Returns only the direct content of the section (heading + immediate paragraphs,
    lists, and tables). Children are not included; fetch them separately via their
    own section numbers.

    Args:
        document_id: The guidance document UUID.
        section_number: The hierarchical section number (e.g. "1", "1.2", "1.2.3").
        s3_repo: The S3 repository, injected via FastAPI DI.

    Returns:
        Markdown response with media type text/markdown.

    Raises:
        HTTPException: 422 if section_number is not a valid hierarchical number.
        HTTPException: 404 if the section does not exist.
    """
    try:
        content = await s3_repo.download_section(document_id, section_number)
        return fastapi.Response(
            content=content, media_type="text/markdown; charset=utf-8"
        )
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Section not found",
            ) from exc
        raise
