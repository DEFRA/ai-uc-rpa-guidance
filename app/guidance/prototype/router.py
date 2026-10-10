"""FastAPI router for reading prototype guides unpacked from an uploaded zip.

This is a deliberately parallel surface to app/guidance/documents: prototype
guides are parsed outside this service by scripts/parse_docx.py (in the
rpa-ai-guidance-hub-api repo) and zipped up, rather than going through the
guidance pipeline, so there is no Mongo-backed document record -- only reads
of whatever is currently under prototype_guides/ in the same guidance S3
bucket, a purge that clears that prefix out, and an upload of a zip that
replaces it and builds its manifest (see uploads.py). This whole package can be removed without touching
app/guidance/documents.
"""

import json
import logging
from pathlib import Path
from typing import Annotated

import botocore.exceptions
import fastapi

from app.guidance.documents import api_schemas as document_schemas
from app.guidance.prototype import (
    api_schemas,
    dependencies,
    manifest,
    s3_repository,
    sections,
    unpack,
    uploads,
)

_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"

# Duplicated from app/guidance/documents/router.py rather than imported, so
# that this package stays independent and removable without touching that
# module.
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

router = fastapi.APIRouter(prefix="/prototype/guides", tags=["prototype"])

logger = logging.getLogger(__name__)


@router.get(
    "/manifest",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Prototype guides manifest",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Manifest not found",
        },
    },
)
async def get_manifest(
    s3_repo: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
) -> dict[str, api_schemas.PrototypeGuide]:
    """Return the prototype guides manifest.

    Reads and parses prototype_guides/manifest.json, which maps each guide's
    title slug to its document id and ordered version history. It is built
    when a zip is unpacked (see uploads.py); this endpoint only ever reads it.

    Args:
        s3_repo: The prototype guide S3 repository, injected via FastAPI DI.

    Returns:
        A dict mapping guide name to its manifest entry.

    Raises:
        HTTPException: 404 if there is no manifest: nothing has been
            uploaded, or the guides were purged or replaced by an empty zip.
    """
    try:
        raw = await s3_repo.download_manifest()
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Manifest not found",
            ) from exc
        raise

    data = json.loads(raw)
    return {
        name: api_schemas.PrototypeGuide.model_validate(guide)
        for name, guide in data.items()
    }


@router.get(
    "/{document_id}/content",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Prototype guide Markdown content",
            "content": {"text/markdown": {}},
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Guide or content not found",
        },
    },
)
async def get_content(
    document_id: str,
    s3_repo: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
    version_id: Annotated[
        str | None,
        fastapi.Query(
            description=(
                "Specific version id to read. Omit to resolve the "
                "document's latest version from the manifest."
            )
        ),
    ] = None,
) -> fastapi.Response:
    """Return the Markdown content for a prototype guide version.

    When version_id is given, the manifest is skipped entirely and the
    content is read straight from
    prototype_guides/{document_id}/{version_id}/content.md. When omitted, the
    manifest is fetched and parsed to resolve document_id's latest version.

    Args:
        document_id: The guide's document id (a uuid4 string).
        s3_repo: The prototype guide S3 repository, injected via FastAPI DI.
        version_id: An explicit version id, or None to use the latest.

    Returns:
        Markdown response with media type text/markdown.

    Raises:
        HTTPException: 404 if document_id is not in the manifest (only
            checked when version_id is omitted), or if the resolved content
            object itself does not exist in S3.
    """
    content = await _read_content(s3_repo, document_id, version_id)
    return fastapi.Response(content=content, media_type=_MARKDOWN_MEDIA_TYPE)


@router.get(
    "/{document_id}/sections/{section_number}",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "One section of a prototype guide, as Markdown",
            "content": {"text/markdown": {}},
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Guide, content or section not found",
        },
    },
)
async def get_section(
    document_id: str,
    section_number: str,
    s3_repo: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
    version_id: Annotated[
        str | None,
        fastapi.Query(
            description=(
                "Specific version id to read. Omit to resolve the "
                "document's latest version from the manifest."
            )
        ),
    ] = None,
) -> fastapi.Response:
    """Return one section of a prototype guide version, as Markdown.

    The section is cut from the version's content.md: its heading and its own
    text, up to the next section heading of any level (see sections.py).

    Args:
        document_id: The guide's document id (a uuid4 string).
        section_number: The section's dotted number, or the slug of an
            unnumbered section's heading.
        s3_repo: The prototype guide S3 repository, injected via FastAPI DI.
        version_id: An explicit version id, or None to use the latest.

    Returns:
        Markdown response with media type text/markdown.

    Raises:
        HTTPException: 404 if the guide or its content cannot be found, as
            for /content, or if the guide has no such section.
    """
    content = await _read_content(s3_repo, document_id, version_id)

    section = sections.find(content.decode(), section_number)
    if section is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No section {section_number}",
        )

    return fastapi.Response(content=section.markdown, media_type=_MARKDOWN_MEDIA_TYPE)


@router.get(
    "/{document_id}/assets/{asset_id}",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Asset file",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "Guide, version or asset not found",
        },
    },
)
async def get_asset(
    document_id: str,
    asset_id: str,
    s3_repo: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
    version_id: Annotated[
        str | None,
        fastapi.Query(
            description=(
                "Version id to validate the asset request against. Omit to "
                "validate against the document's latest version."
            )
        ),
    ] = None,
) -> fastapi.Response:
    """Return the raw bytes for an asset (e.g. an image) from a prototype guide.

    Assets sit one level above versions in S3
    (prototype_guides/{document_id}/assets/{asset_id}) and are
    content-addressed by digest, so every version of a guide shares the same
    asset store: re-parsing a guide that keeps a picture never duplicates it.
    version_id is used only to confirm the document/version pair exists in
    the manifest -- passing a different, equally valid version_id for the
    same document_id must return identical bytes, it does not select a
    different asset.

    Args:
        document_id: The guide's document id (a uuid4 string).
        asset_id: The asset filename, e.g. a content-addressed digest with
            its extension.
        s3_repo: The prototype guide S3 repository, injected via FastAPI DI.
        version_id: An explicit version id to validate, or None to validate
            against the latest version.

    Returns:
        Asset bytes with media type inferred from the file extension,
        falling back to application/octet-stream.

    Raises:
        HTTPException: 404 if the document/version pair does not exist in
            the manifest, or if the asset object itself does not exist in
            S3.
    """
    try:
        raw_manifest = await s3_repo.download_manifest()
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail=f"No guide {document_id}",
            ) from exc
        raise

    parsed_manifest = json.loads(raw_manifest)
    resolved_version_id = manifest.resolve_version_id(
        parsed_manifest, document_id, version_id
    )
    if resolved_version_id is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {document_id}",
        )

    try:
        data = await s3_repo.download_asset(document_id, asset_id)
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            ) from exc
        raise

    ext = Path(asset_id).suffix.lower()
    media_type = _IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")
    return fastapi.Response(content=data, media_type=media_type)


@router.delete(
    "",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Every prototype guide object deleted",
        },
        fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "S3 reported some objects as not deleted",
        },
    },
)
async def purge(
    s3_repo: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
) -> api_schemas.PurgeResult:
    """Delete every prototype guide, version, asset and the manifest.

    Irreversible: clears the whole prototype_guides/ prefix. Purging an
    already-empty prefix succeeds and deletes nothing.

    Args:
        s3_repo: The prototype guide S3 repository, injected via FastAPI DI.

    Returns:
        How many objects were deleted.

    Raises:
        HTTPException: 500 if S3 reported any object as not deleted.
    """
    try:
        deleted = await s3_repo.purge()
    except s3_repository.PurgeIncompleteError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return api_schemas.PurgeResult(deleted=deleted)


@router.post(
    "/uploads",
    status_code=fastapi.status.HTTP_201_CREATED,
    responses={
        fastapi.status.HTTP_502_BAD_GATEWAY: {
            "description": "CDP uploader is unavailable or returned an error",
        },
    },
)
async def initiate_upload(
    payload: api_schemas.UploadRequest,
) -> api_schemas.UploadResponse:
    """Open a CDP uploader session for a zip that replaces every prototype guide.

    Args:
        payload: Where CDP uploader sends the browser once the file is sent.

    Returns:
        The upload id the browser posts the zip against.

    Raises:
        HTTPException: 502 if CDP uploader fails.
    """
    try:
        upload_id = await uploads.initiate_upload(payload.redirect)
    except Exception as exc:
        logger.exception("Failed to initiate prototype guides upload")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_502_BAD_GATEWAY,
            detail="Failed to initiate upload with CDP uploader service",
        ) from exc

    return api_schemas.UploadResponse(upload_id=upload_id)


@router.post(
    "/uploads/callback",
    status_code=fastapi.status.HTTP_204_NO_CONTENT,
)
async def handle_upload_callback(
    payload: document_schemas.CdpUploaderStatusPayload,
    s3_repo: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(dependencies.get_s3_repository),
    ],
) -> None:
    """Replace the prototype guides with the zip CDP uploader delivered.

    A zip that is not one of prototype guides is still acknowledged with a
    204: CDP uploader retries any callback that fails, and retrying cannot
    make a bad zip good. It is logged, and the current guides are left as
    they are.

    Args:
        payload: CDP uploader's callback.
        s3_repo: The prototype guide S3 repository, injected via FastAPI DI.
    """
    try:
        await uploads.handle_callback(payload, s3_repo)
    except unpack.InvalidGuidesZipError as exc:
        logger.warning("Rejected prototype guides upload: %s", exc)


async def _read_content(
    s3_repo: s3_repository.PrototypeGuideS3Repository,
    document_id: str,
    version_id: str | None,
) -> bytes:
    """Download a guide version's content.md, the latest if no version is given.

    Args:
        s3_repo: The prototype guide S3 repository.
        document_id: The guide's document id.
        version_id: An explicit version id, or None to use the latest.

    Returns:
        The raw Markdown bytes.

    Raises:
        HTTPException: 404 if document_id is not in the manifest (only
            checked when version_id is omitted), or if the content object
            itself does not exist in S3.
    """
    resolved_version_id = version_id

    if resolved_version_id is None:
        resolved_version_id = await _resolve_latest_version_id(s3_repo, document_id)

    try:
        return await s3_repo.download_content(document_id, resolved_version_id)
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Content not found",
            ) from exc
        raise


async def _resolve_latest_version_id(
    s3_repo: s3_repository.PrototypeGuideS3Repository, document_id: str
) -> str:
    """Fetch and parse the manifest, resolving document_id's latest version id.

    Args:
        s3_repo: The prototype guide S3 repository.
        document_id: The guide's document id.

    Returns:
        The latest version id for document_id.

    Raises:
        HTTPException: 404 if document_id is not present in the manifest.
    """
    try:
        raw_manifest = await s3_repo.download_manifest()
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail=f"No guide {document_id}",
            ) from exc
        raise

    parsed_manifest = json.loads(raw_manifest)
    resolved_version_id = manifest.resolve_version_id(parsed_manifest, document_id)
    if resolved_version_id is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No guide {document_id}",
        )

    return resolved_version_id
