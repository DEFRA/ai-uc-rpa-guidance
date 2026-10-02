"""Tests for replacing prototype guides from a CDP uploader callback."""

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.guidance.documents import api_schemas as document_schemas
from app.guidance.prototype import unpack, uploads


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _callback(
    file_status: str = "complete",
) -> document_schemas.CdpUploaderStatusPayload:
    return document_schemas.CdpUploaderStatusPayload.model_validate(
        {
            "uploadStatus": "ready",
            "form": {
                "file": {
                    "fileId": "file-1",
                    "filename": "guides.zip",
                    "fileStatus": file_status,
                    "contentLength": 10,
                    "checksumSha256": "abc",
                    "s3Key": "prototype_uploads/file-1",
                    "s3Bucket": "guidance-bucket",
                }
            },
        }
    )


def _repo(zip_bytes: bytes) -> MagicMock:
    repo = MagicMock()
    repo.download_upload = AsyncMock(return_value=zip_bytes)
    repo.purge = AsyncMock(return_value=5)
    repo.upload_files = AsyncMock()
    return repo


class TestHandleCallback:
    """Test handle_callback."""

    async def test_purges_then_writes_the_unpacked_guides(self) -> None:
        repo = _repo(_zip({"manifest.json": b"{}", "doc/v/content.md": b"# G"}))
        calls = MagicMock()
        calls.attach_mock(repo.purge, "purge")
        calls.attach_mock(repo.upload_files, "upload_files")

        written = await uploads.handle_callback(_callback(), repo)

        assert written == 2
        repo.download_upload.assert_awaited_once_with(
            "guidance-bucket", "prototype_uploads/file-1"
        )
        assert [name for name, *_ in calls.mock_calls] == ["purge", "upload_files"]
        repo.upload_files.assert_awaited_once_with(
            {"manifest.json": b"{}", "doc/v/content.md": b"# G"}
        )

    async def test_leaves_the_guides_alone_when_the_zip_is_invalid(self) -> None:
        repo = _repo(_zip({"doc/v/content.md": b"# G"}))

        with pytest.raises(unpack.InvalidGuidesZipError):
            await uploads.handle_callback(_callback(), repo)

        repo.purge.assert_not_awaited()
        repo.upload_files.assert_not_awaited()

    async def test_does_nothing_for_a_rejected_file(self) -> None:
        repo = _repo(b"")

        written = await uploads.handle_callback(_callback("rejected"), repo)

        assert written == 0
        repo.download_upload.assert_not_awaited()
        repo.purge.assert_not_awaited()
