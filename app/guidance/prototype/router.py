"""FastAPI router for reading prototype guides synced by hand into S3.

This is a deliberately parallel, read-only surface to app/guidance/documents:
prototype guides are produced outside this service by
scripts/parse_docx_for_s3.py (in the rpa-ai-guidance-hub-api repo) and pushed
with a manual `aws s3 sync` rather than the CDP-uploader pipeline, so there is
no Mongo-backed document record, no upload/callback flow, and no writer here
-- only reads of whatever is currently under prototype_guides/ in the same
guidance S3 bucket. This whole package can be removed without touching
app/guidance/documents.
"""

import json
import logging
from pathlib import Path
from typing import Annotated

import botocore.exceptions
import fastapi

from app.guidance.prototype import api_schemas, dependencies, manifest, s3_repository

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
    human name to its document id and ordered version history. This is
    produced by scripts/parse_docx_for_s3.py and synced up by hand; this
    endpoint only ever reads it.

    Args:
        s3_repo: The prototype guide S3 repository, injected via FastAPI DI.

    Returns:
        A dict mapping guide name to its manifest entry.

    Raises:
        HTTPException: 404 if the manifest has not been synced yet.
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
    resolved_version_id = version_id

    if resolved_version_id is None:
        resolved_version_id = await _resolve_latest_version_id(s3_repo, document_id)

    try:
        content = await s3_repo.download_content(document_id, resolved_version_id)
        return fastapi.Response(content=content, media_type=_MARKDOWN_MEDIA_TYPE)
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Content not found",
            ) from exc
        raise


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
