"""FastAPI router for the guidance document management endpoints."""

import logging
import uuid
from pathlib import Path
from typing import Annotated

import botocore.exceptions
import fastapi

from app.guidance.documents import api_schemas, dependencies, s3_repository, service

_IMAGE_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

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
    "/{document_id}/content",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Full document Markdown content",
            "content": {"text/markdown": {}},
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Content not found — document may not have completed processing",
        },
    },
)
async def get_document_content(
    document_id: uuid.UUID,
    s3_repo: Annotated[
        s3_repository.AbstractGuidanceStorageRepository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
) -> fastapi.Response:
    """Return the full rendered Markdown for a parsed guidance document.

    Args:
        document_id: The guidance document UUID.
        s3_repo: The S3 repository, injected via FastAPI DI.

    Returns:
        Markdown response with media type text/markdown.

    Raises:
        HTTPException: 404 if the document content has not been produced yet.
    """
    try:
        content = await s3_repo.download_content(document_id)
        return fastapi.Response(
            content=content, media_type="text/markdown; charset=utf-8"
        )
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Content not found",
            ) from exc
        raise


def _section_and_descendant_numbers(
    manifest: api_schemas.DocumentManifestResponse, section_number: str
) -> list[str]:
    """Return section_number followed by all its descendants, in document order.

    Traverses the manifest's parent->children adjacency depth-first, so the
    result is exactly the contiguous run of section numbers that a section and
    its nested children occupy within the full document.
    """
    nodes_by_number = {node.number: node for node in manifest.sections}
    if section_number not in nodes_by_number:
        return [section_number]

    ordered: list[str] = []

    def visit(number: str) -> None:
        ordered.append(number)
        node = nodes_by_number.get(number)
        if node is None:
            return
        for child_number in node.children:
            visit(child_number)

    visit(section_number)
    return ordered


@router.get(
    "/{document_id}/sections/{section_number}",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Section Markdown content",
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
    children: Annotated[
        bool,
        fastapi.Query(
            description="Include the content of all descendant sections, "
            "equivalent to slicing this section out of the full document"
        ),
    ] = False,
) -> fastapi.Response:
    """Return the Markdown content for a document section.

    By default returns only the direct content of the section (heading +
    immediate paragraphs, lists, and tables), with children fetched separately
    via their own section numbers. With children=true, descendant sections'
    content is appended, matching what slicing this section (and its children)
    out of the full /content document would produce.

    Args:
        document_id: The guidance document UUID.
        section_number: The hierarchical section number (e.g. "1", "1.2", "1.2.3").
        s3_repo: The S3 repository, injected via FastAPI DI.
        children: Whether to include descendant sections' content.

    Returns:
        Markdown response with media type text/markdown.

    Raises:
        HTTPException: 422 if section_number is not a valid hierarchical number.
        HTTPException: 404 if the section does not exist.
    """
    try:
        if not children:
            content = await s3_repo.download_section(document_id, section_number)
            return fastapi.Response(
                content=content, media_type="text/markdown; charset=utf-8"
            )

        raw_manifest = await s3_repo.download_manifest(document_id)
        manifest = api_schemas.DocumentManifestResponse.model_validate_json(
            raw_manifest
        )
        section_numbers = _section_and_descendant_numbers(manifest, section_number)

        parts = [
            (await s3_repo.download_section(document_id, number)).rstrip("\n")
            for number in section_numbers
        ]
        content = "\n\n".join(parts) + "\n"

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


@router.get(
    "/{document_id}/images/{filename}",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Image file",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Image not found",
        },
    },
)
async def get_document_image(
    document_id: uuid.UUID,
    filename: Annotated[
        str,
        fastapi.Path(
            pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
            description="Image filename, e.g. img_1.png",
        ),
    ],
    s3_repo: Annotated[
        s3_repository.AbstractGuidanceStorageRepository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
) -> fastapi.Response:
    """Return the raw bytes for an image extracted from a guidance document.

    Args:
        document_id: The guidance document UUID.
        filename: The image filename (e.g. "img_1.png").
        s3_repo: The S3 repository, injected via FastAPI DI.

    Returns:
        Image bytes with the appropriate media type.

    Raises:
        HTTPException: 404 if the image does not exist.
    """
    try:
        data = await s3_repo.download_image(document_id, filename)
        ext = Path(filename).suffix.lower()
        media_type = _IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")
        return fastapi.Response(content=data, media_type=media_type)
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Image not found",
            ) from exc
        raise
