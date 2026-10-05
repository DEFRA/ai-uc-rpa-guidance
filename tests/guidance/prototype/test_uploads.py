"""Tests for replacing prototype guides from a CDP uploader callback."""

import io
import json
import zipfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.guidance.documents import api_schemas as document_schemas
from app.guidance.prototype import s3_repository, unpack, uploads


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return buffer.getvalue()


_BUCKET = uploads.settings.guidance_s3_bucket
_GUIDE = "doc/v/content.md"
_KEY = "prototype_uploads/upload-1/file-1"


def _callback(
    file_status: str = "complete",
    bucket: str = _BUCKET,
    key: str = _KEY,
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
                    "s3Key": key,
                    "s3Bucket": bucket,
                }
            },
        }
    )


def _repo(zip_bytes: bytes) -> MagicMock:
    """A repository serving `zip_bytes` as the upload, recording what it writes."""
    repo = MagicMock()
    repo.written = {}

    async def open_upload(_bucket: str, _key: str) -> io.BytesIO:
        return io.BytesIO(zip_bytes)

    async def upload_stream(name: str, stream: Any) -> None:
        repo.written[name] = stream.read()

    repo.open_upload = AsyncMock(side_effect=open_upload)
    repo.purge = AsyncMock(return_value=5)
    repo.upload_stream = AsyncMock(side_effect=upload_stream)
    repo.delete_upload = AsyncMock()
    return repo


def _mock_uploader(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post.return_value = response
    return client


class TestInitiateUpload:
    """Test initiate_upload."""

    async def test_opens_a_zip_only_session_that_calls_back_here(self) -> None:
        response = MagicMock()
        response.json.return_value = {"uploadId": "upload-123"}
        client = _mock_uploader(response)

        with patch.object(
            uploads.http_client, "create_async_client", return_value=client
        ):
            upload_id = await uploads.initiate_upload("/admin/prototype-guides")

        assert upload_id == "upload-123"
        body = client.post.call_args.kwargs["json"]
        assert client.post.call_args.args[0].endswith("/initiate")
        assert body["redirect"] == "/admin/prototype-guides"
        assert body["s3Path"] == uploads.UPLOAD_PATH
        assert body["mimeTypes"] == ["application/zip", "application/x-zip-compressed"]
        assert body["maxFileSize"] == uploads.MAX_UPLOAD_BYTES
        assert body["callback"].endswith("/prototype/guides/uploads/callback")

    async def test_raises_when_the_uploader_fails(self) -> None:
        response = MagicMock()
        response.raise_for_status.side_effect = RuntimeError("HTTP 502")
        client = _mock_uploader(response)

        with (
            patch.object(
                uploads.http_client, "create_async_client", return_value=client
            ),
            pytest.raises(RuntimeError, match="HTTP 502"),
        ):
            await uploads.initiate_upload("/admin/prototype-guides")


class TestHandleCallback:
    """Test handle_callback."""

    async def test_verifies_purges_streams_the_guides_then_deletes_the_zip(
        self,
    ) -> None:
        repo = _repo(_zip({_GUIDE: b"# G", "doc/assets/a.png": b"\x89PNG"}))
        calls = MagicMock()
        calls.attach_mock(repo.purge, "purge")
        calls.attach_mock(repo.upload_stream, "upload_stream")
        calls.attach_mock(repo.delete_upload, "delete_upload")

        written = await uploads.handle_callback(_callback(), repo)

        assert written == 2
        repo.open_upload.assert_awaited_once_with(_BUCKET, _KEY)
        steps = [
            (step, args[0] if step == "upload_stream" else None)
            for step, args, _ in calls.mock_calls
        ]
        assert steps == [
            ("purge", None),
            ("upload_stream", _GUIDE),
            ("upload_stream", "doc/assets/a.png"),
            ("upload_stream", "manifest.json"),
            ("delete_upload", None),
        ]
        assert repo.written[_GUIDE] == b"# G"
        repo.delete_upload.assert_awaited_once_with(_BUCKET, _KEY)

    async def test_writes_a_manifest_built_from_the_zip(self) -> None:
        repo = _repo(
            _zip({_GUIDE: b"# Claims Guide\n\n## 1 Scope\n", "manifest.json": b"{}"})
        )

        await uploads.handle_callback(_callback(), repo)

        built = json.loads(repo.written["manifest.json"])
        assert list(built) == ["claims-guide"]
        guide = built["claims-guide"]
        assert guide["documentId"] == "doc"
        assert guide["latestVersion"] == 1
        assert guide["versions"][0]["versionId"] == "v"
        assert guide["versions"][0]["contentUrl"] == _GUIDE
        assert guide["versions"][0]["sections"] == 1

    async def test_an_empty_zip_purges_the_guides_and_writes_no_manifest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = _repo(_zip({}))

        written = await uploads.handle_callback(_callback(), repo)

        assert written == 0
        repo.purge.assert_awaited_once()
        repo.upload_stream.assert_not_awaited()
        repo.delete_upload.assert_awaited_once_with(_BUCKET, _KEY)
        assert "holds no guides" in caplog.text

    async def test_leaves_the_guides_alone_and_deletes_an_invalid_zip(self) -> None:
        repo = _repo(_zip({"notes.txt": b"x"}))
        callback = _callback()

        with pytest.raises(unpack.InvalidGuidesZipError, match="no guides"):
            await uploads.handle_callback(callback, repo)

        repo.purge.assert_not_awaited()
        repo.upload_stream.assert_not_awaited()
        repo.delete_upload.assert_awaited_once_with(_BUCKET, _KEY)

    async def test_deletes_a_file_that_is_not_a_zip(self) -> None:
        repo = _repo(b"not a zip")
        callback = _callback()

        with pytest.raises(unpack.InvalidGuidesZipError, match="not a zip"):
            await uploads.handle_callback(callback, repo)

        repo.purge.assert_not_awaited()
        repo.delete_upload.assert_awaited_once_with(_BUCKET, _KEY)

    async def test_finds_a_corrupt_entry_before_purging(self) -> None:
        zip_bytes = bytearray(_zip({_GUIDE: b"# " + b"x" * 200}))
        offset = zip_bytes.index(_GUIDE.encode()) + len(_GUIDE) + 2
        zip_bytes[offset] ^= 0xFF
        repo = _repo(bytes(zip_bytes))
        callback = _callback()

        with pytest.raises(unpack.InvalidGuidesZipError, match="corrupt"):
            await uploads.handle_callback(callback, repo)

        repo.purge.assert_not_awaited()
        repo.delete_upload.assert_awaited_once_with(_BUCKET, _KEY)

    async def test_treats_an_unreadable_upload_as_corrupt(self) -> None:
        repo = _repo(b"")
        repo.open_upload.side_effect = OSError("Cannot read s3://bucket/key")
        callback = _callback()

        with pytest.raises(unpack.InvalidGuidesZipError, match="cannot be read"):
            await uploads.handle_callback(callback, repo)

        repo.purge.assert_not_awaited()
        repo.delete_upload.assert_awaited_once_with(_BUCKET, _KEY)

    async def test_keeps_the_zip_when_replacing_the_guides_fails(self) -> None:
        repo = _repo(_zip({_GUIDE: b"# G"}))
        repo.purge.side_effect = RuntimeError("S3 unavailable")
        callback = _callback()

        with pytest.raises(RuntimeError, match="S3 unavailable"):
            await uploads.handle_callback(callback, repo)

        repo.delete_upload.assert_not_awaited()

    async def test_keeps_the_zip_and_writes_nothing_when_the_purge_is_incomplete(
        self,
    ) -> None:
        repo = _repo(_zip({_GUIDE: b"# G"}))
        repo.purge.side_effect = s3_repository.PurgeIncompleteError(["k"], 3)
        callback = _callback()

        with pytest.raises(s3_repository.PurgeIncompleteError):
            await uploads.handle_callback(callback, repo)

        repo.upload_stream.assert_not_awaited()
        repo.delete_upload.assert_not_awaited()

    @pytest.mark.parametrize(
        ("bucket", "key"),
        [
            pytest.param("someone-elses-bucket", _KEY, id="other-bucket"),
            pytest.param(_BUCKET, "parsed_guidance/doc/content.md", id="other-prefix"),
            pytest.param(_BUCKET, "prototype_uploadsX/file", id="prefix-lookalike"),
        ],
    )
    async def test_ignores_a_file_outside_the_upload_prefix(
        self, bucket: str, key: str
    ) -> None:
        repo = _repo(_zip({_GUIDE: b"# G"}))

        written = await uploads.handle_callback(_callback(bucket=bucket, key=key), repo)

        assert written == 0
        repo.open_upload.assert_not_awaited()
        repo.delete_upload.assert_not_awaited()
        repo.purge.assert_not_awaited()

    async def test_does_nothing_for_a_rejected_file(self) -> None:
        repo = _repo(b"")

        written = await uploads.handle_callback(_callback("rejected"), repo)

        assert written == 0
        repo.open_upload.assert_not_awaited()
        repo.purge.assert_not_awaited()
        repo.delete_upload.assert_not_awaited()
