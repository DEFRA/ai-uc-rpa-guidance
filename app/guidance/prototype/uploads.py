"""Replacing the prototype guides with a zip uploaded through CDP uploader.

The zip goes the same way every upload does -- virus-scanned by CDP uploader
and delivered to the guidance bucket, under its own prototype_uploads/ path --
and the callback unpacks it into prototype_guides/. An upload *replaces* the
guides: the old ones are purged first, so the manifest and the files beside
it always come from one zip. The manifest is built here, from the versions in
the zip (see manifest.build), and written after the files it names. A zip with
no files purges the guides and writes no manifest.

The zip is read where it lies, by byte range, and one entry at a time, so
neither it nor its contents are ever held in memory, bar each version's
content.md, which is read whole to build the manifest. It is checked in full
before anything is purged -- its index first, then every entry read through
and discarded -- so a bad upload leaves the current guides where they are.
Any problem reading it is taken to mean the zip is corrupt.

The zip itself is deleted once it has been dealt with -- unpacked, or found
not to be a zip of guides -- so nothing is left under prototype_uploads/. Any
other failure keeps it, so CDP uploader's retry of the callback can try again.
The callback is unauthenticated and names its own bucket and key, so only a
file under prototype_uploads/ in the guidance bucket is ever read or deleted.
"""

import asyncio
import io
import json
import logging
from typing import Any

from app import config
from app.common import http_client
from app.guidance.documents import api_schemas as document_schemas
from app.guidance.prototype import manifest, s3_repository, unpack

logger = logging.getLogger(__name__)

settings = config.get_config()

UPLOAD_PATH = "prototype_uploads"

_ZIP_MIME_TYPES = ["application/zip", "application/x-zip-compressed"]

# Ten times the guides as first uploaded (a 35 MB zip); CDP uploader rejects
# anything larger before it reaches the bucket.
MAX_UPLOAD_BYTES = 350_000_000


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
                "maxFileSize": MAX_UPLOAD_BYTES,
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
            Nothing has been purged when this is raised, and the zip has
            been deleted.
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

    if upload.s3_bucket != settings.guidance_s3_bucket or not upload.s3_key.startswith(
        f"{UPLOAD_PATH}/"
    ):
        logger.warning(
            "Ignored a prototype guides callback for s3://%s/%s, outside %s/",
            upload.s3_bucket,
            upload.s3_key,
            UPLOAD_PATH,
        )
        return 0

    try:
        written, purged = await _replace_guides(upload, s3_repo)
    except unpack.InvalidGuidesZipError:
        await s3_repo.delete_upload(upload.s3_bucket, upload.s3_key)
        raise

    await s3_repo.delete_upload(upload.s3_bucket, upload.s3_key)

    logger.info(
        "Replaced %d prototype guide objects with %d from %s",
        purged,
        written,
        upload.filename,
    )

    return written


async def _replace_guides(
    upload: document_schemas.FileUploadDetail,
    s3_repo: s3_repository.PrototypeGuideS3Repository,
) -> tuple[int, int]:
    """Check the uploaded zip in full, then purge and stream its entries in.

    Returns:
        How many files were unpacked (the manifest aside), and how many objects
        were purged.

    Raises:
        InvalidGuidesZipError: If the zip cannot be read or fails a check.
    """
    try:
        source = await s3_repo.open_upload(upload.s3_bucket, upload.s3_key)
    except OSError as exc:
        msg = f"The upload cannot be read: {exc}"
        raise unpack.InvalidGuidesZipError(msg) from exc

    with source, await asyncio.to_thread(unpack.GuidesZip, source) as guides:
        verified = await asyncio.to_thread(guides.verify)
        logger.info(
            "Verified %d files (%d bytes unzipped) in %s",
            len(guides.entries),
            verified,
            upload.filename,
        )

        built = await asyncio.to_thread(_manifest_of, guides)

        purged = await s3_repo.purge()

        for entry in guides.entries:
            stream = await asyncio.to_thread(guides.open, entry)
            try:
                await s3_repo.upload_stream(entry.name, stream)
            finally:
                stream.close()

    if built:
        body = json.dumps(built, indent=2).encode() + b"\n"
        await s3_repo.upload_stream(unpack.MANIFEST_NAME, io.BytesIO(body))
    else:
        logger.warning(
            "%s holds no guides: every prototype guide was purged and no "
            "manifest was written",
            upload.filename,
        )

    return len(guides.entries), purged


def _manifest_of(guides: unpack.GuidesZip) -> dict[str, Any]:
    """Build the manifest from every version's content.md in the zip."""
    return manifest.build(
        [
            manifest.GuideVersion(
                document_id=version.document_id,
                version_id=version.version_id,
                modified=version.modified,
                markdown=_read(guides, version.entry),
            )
            for version in guides.versions
        ]
    )


def _read(guides: unpack.GuidesZip, entry: unpack.GuideEntry) -> str:
    stream = guides.open(entry)
    try:
        return stream.read().decode("utf-8", errors="replace")
    finally:
        stream.close()
