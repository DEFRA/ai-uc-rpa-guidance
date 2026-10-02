"""Replacing the prototype guides with a zip uploaded through CDP uploader.

The zip goes the same way every upload does -- virus-scanned by CDP uploader
and delivered to the guidance bucket, under its own prototype_uploads/ path --
and the callback unpacks it into prototype_guides/. An upload *replaces* the
guides: the old ones are purged first, so the manifest and the files beside
it always come from one zip. The zip is checked before anything is purged, so
a bad upload leaves the current guides where they are.
"""

import logging

from app import config
from app.common import http_client
from app.guidance.documents import api_schemas as document_schemas
from app.guidance.prototype import s3_repository, unpack

logger = logging.getLogger(__name__)

settings = config.get_config()

UPLOAD_PATH = "prototype_uploads"

_ZIP_MIME_TYPES = ["application/zip", "application/x-zip-compressed"]


async def initiate_upload(redirect: str) -> str:
    """Open a CDP uploader session for a zip of prototype guides.

    Args:
        redirect: Where CDP uploader sends the browser once the file is sent.

    Returns:
        The upload id the browser posts the file against.

    Raises:
        httpx.HTTPStatusError: If CDP uploader returns an error.
    """
    async with http_client.create_async_client(settings.cdp_uploader_timeout) as client:
        resp = await client.post(
            f"{settings.cdp_uploader_base_url}/initiate",
            json={
                "redirect": redirect,
                "s3Bucket": settings.guidance_s3_bucket,
                "s3Path": UPLOAD_PATH,
                "mimeTypes": _ZIP_MIME_TYPES,
                "callback": f"{settings.callback_base_url}/prototype/guides/uploads/callback",
            },
        )
        resp.raise_for_status()

    upload_id: str = document_schemas.DocumentUploadResponse(**resp.json()).upload_id

    logger.info("Initiated prototype guides upload session %s", upload_id)

    return upload_id


async def handle_callback(
    payload: document_schemas.CdpUploaderStatusPayload,
    s3_repo: s3_repository.PrototypeGuideS3Repository,
) -> int:
    """Replace the prototype guides with the zip CDP uploader delivered.

    Args:
        payload: CDP uploader's callback.
        s3_repo: The prototype guide S3 repository.

    Returns:
        The number of files unpacked; 0 if no clean file was delivered.

    Raises:
        InvalidGuidesZipError: If the file is not a zip of prototype guides.
            Nothing has been purged when this is raised.
    """
    upload = next(
        (
            value
            for value in payload.form.values()
            if isinstance(value, document_schemas.FileUploadDetail)
        ),
        None,
    )

    if upload is None or upload.file_status != "complete":
        logger.warning(
            "Prototype guides upload delivered no clean file (status %s, %d rejected)",
            payload.upload_status,
            payload.number_of_rejected_files,
        )
        return 0

    zip_bytes = await s3_repo.download_upload(upload.s3_bucket, upload.s3_key)
    files = unpack.unpack(zip_bytes)

    purged = await s3_repo.purge()
    await s3_repo.upload_files(files)

    logger.info(
        "Replaced %d prototype guide objects with %d from %s",
        purged,
        len(files),
        upload.filename,
    )

    return len(files)
