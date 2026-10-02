"""Tests for PrototypeGuideS3Repository."""

import io
from unittest.mock import MagicMock

import pytest

from app.guidance.prototype import s3_repository

_BUCKET = "test-guidance-bucket"
_DOCUMENT_ID = "2403b062-1ca7-4ef7-9df1-87669c51b281"
_VERSION_ID = "d37b0ccf-0000-0000-0000-000000000001"


def _mock_s3_client(body: bytes) -> MagicMock:
    client = MagicMock()
    response_body = MagicMock()
    response_body.read.return_value = body
    client.get_object.return_value = {"Body": response_body}
    return client


class TestDownloadManifest:
    """Test download_manifest."""

    async def test_downloads_manifest_from_expected_key(self) -> None:
        client = _mock_s3_client(b'{"claims-guide": {}}')
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        body = await repo.download_manifest()

        assert body == b'{"claims-guide": {}}'
        client.get_object.assert_called_once_with(
            Bucket=_BUCKET, Key="prototype_guides/manifest.json"
        )


class TestDownloadContent:
    """Test download_content."""

    async def test_downloads_content_from_expected_key(self) -> None:
        client = _mock_s3_client(b"# Title")
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        body = await repo.download_content(_DOCUMENT_ID, _VERSION_ID)

        assert body == b"# Title"
        client.get_object.assert_called_once_with(
            Bucket=_BUCKET,
            Key=f"prototype_guides/{_DOCUMENT_ID}/{_VERSION_ID}/content.md",
        )


class TestDownloadAsset:
    """Test download_asset."""

    async def test_downloads_asset_from_expected_key(self) -> None:
        client = _mock_s3_client(b"\x89PNG")
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        body = await repo.download_asset(_DOCUMENT_ID, "digest123.png")

        assert body == b"\x89PNG"
        client.get_object.assert_called_once_with(
            Bucket=_BUCKET,
            Key=f"prototype_guides/{_DOCUMENT_ID}/assets/digest123.png",
        )

    async def test_asset_key_does_not_include_version(self) -> None:
        client = _mock_s3_client(b"\x89PNG")
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        await repo.download_asset(_DOCUMENT_ID, "digest123.png")

        called_key = client.get_object.call_args.kwargs["Key"]
        assert _VERSION_ID not in called_key
        assert "assets" in called_key


def _mock_paginated_s3_client(pages: list[list[str]]) -> MagicMock:
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": key} for key in keys]} if keys else {} for keys in pages
    ]
    return client


class TestPurge:
    """Test purge."""

    async def test_deletes_every_object_under_the_prefix(self) -> None:
        keys = [
            "prototype_guides/manifest.json",
            f"prototype_guides/{_DOCUMENT_ID}/{_VERSION_ID}/content.md",
            f"prototype_guides/{_DOCUMENT_ID}/assets/digest123.png",
        ]
        client = _mock_paginated_s3_client([keys])
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        deleted = await repo.purge()

        assert deleted == 3
        client.get_paginator.return_value.paginate.assert_called_once_with(
            Bucket=_BUCKET, Prefix="prototype_guides/"
        )
        client.delete_objects.assert_called_once_with(
            Bucket=_BUCKET,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
        )

    async def test_deletes_in_batches_of_at_most_1000(self) -> None:
        keys = [f"prototype_guides/doc/assets/{n}.png" for n in range(2500)]
        client = _mock_paginated_s3_client([keys[:1000], keys[1000:2000], keys[2000:]])
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        deleted = await repo.purge()

        assert deleted == 2500
        batch_sizes = [
            len(call.kwargs["Delete"]["Objects"])
            for call in client.delete_objects.call_args_list
        ]
        assert batch_sizes == [1000, 1000, 500]

    async def test_deletes_nothing_when_prefix_is_empty(self) -> None:
        client = _mock_paginated_s3_client([[]])
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        deleted = await repo.purge()

        assert deleted == 0
        client.delete_objects.assert_not_called()


class TestOpenUpload:
    """Test open_upload."""

    async def test_opens_the_bucket_and_key_given_for_ranged_reads(self) -> None:
        client = MagicMock()
        client.head_object.return_value = {"ContentLength": 2}
        body = MagicMock()
        body.read.return_value = b"PK"
        client.get_object.return_value = {"Body": body}
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        reader = await repo.open_upload("upload-bucket", "prototype_uploads/u/f")

        assert reader.read() == b"PK"
        client.head_object.assert_called_once_with(
            Bucket="upload-bucket", Key="prototype_uploads/u/f"
        )
        client.get_object.assert_called_once_with(
            Bucket="upload-bucket", Key="prototype_uploads/u/f", Range="bytes=0-1"
        )


class TestUploadStream:
    """Test upload_stream."""

    @pytest.mark.parametrize(
        ("name", "content_type"),
        [
            ("manifest.json", "application/json"),
            (
                f"{_DOCUMENT_ID}/{_VERSION_ID}/content.md",
                "text/markdown; charset=utf-8",
            ),
            (f"{_DOCUMENT_ID}/assets/digest.png", "image/png"),
            (f"{_DOCUMENT_ID}/assets/digest.unknownext", "application/octet-stream"),
        ],
    )
    async def test_streams_the_file_under_the_prefix_with_its_type(
        self, name: str, content_type: str
    ) -> None:
        client = MagicMock()
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)
        stream = io.BytesIO(b"body")

        await repo.upload_stream(name, stream)

        client.upload_fileobj.assert_called_once_with(
            Fileobj=stream,
            Bucket=_BUCKET,
            Key=f"prototype_guides/{name}",
            ExtraArgs={"ContentType": content_type},
        )


class TestDeleteUpload:
    """Test delete_upload."""

    async def test_deletes_the_bucket_and_key_given(self) -> None:
        client = MagicMock()
        repo = s3_repository.PrototypeGuideS3Repository(client, _BUCKET)

        await repo.delete_upload("upload-bucket", "prototype_uploads/u/f")

        client.delete_object.assert_called_once_with(
            Bucket="upload-bucket", Key="prototype_uploads/u/f"
        )
