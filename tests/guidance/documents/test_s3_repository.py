"""Tests for GuidanceS3Repository."""

import uuid
from unittest.mock import MagicMock

import pytest

from app.guidance.documents import s3_repository


@pytest.fixture
def mock_s3() -> MagicMock:
    return MagicMock()


@pytest.fixture
def repo(mock_s3: MagicMock) -> s3_repository.GuidanceS3Repository:
    return s3_repository.GuidanceS3Repository(mock_s3, "guidance-bucket")


class TestDownloadDocx:
    @pytest.mark.asyncio
    async def test_returns_bytes(
        self, repo: s3_repository.GuidanceS3Repository, mock_s3: MagicMock
    ) -> None:
        content = b"docx content"
        body_mock = MagicMock()
        body_mock.read.return_value = content
        mock_s3.get_object.return_value = {"Body": body_mock}

        result = await repo.download_docx("doc-id/file-id")

        mock_s3.get_object.assert_called_once_with(
            Bucket="guidance-bucket", Key="doc-id/file-id"
        )
        assert result == content

    @pytest.mark.asyncio
    async def test_returns_raw_bytes(
        self, repo: s3_repository.GuidanceS3Repository, mock_s3: MagicMock
    ) -> None:
        body_mock = MagicMock()
        body_mock.read.return_value = b"bytes"
        mock_s3.get_object.return_value = {"Body": body_mock}

        result = await repo.download_docx("some/key")

        mock_s3.get_object.assert_called_once_with(
            Bucket="guidance-bucket", Key="some/key"
        )
        assert result == b"bytes"


class TestUploadContent:
    @pytest.mark.asyncio
    async def test_uploads_to_correct_key(
        self, repo: s3_repository.GuidanceS3Repository, mock_s3: MagicMock
    ) -> None:
        doc_id = uuid.UUID("507f1f77-bcf8-6cd7-9943-9011aabbccdd")
        await repo.upload_content(doc_id, "# Hello\n")

        mock_s3.put_object.assert_called_once_with(
            Bucket="guidance-bucket",
            Key=f"parsed_guidance/{doc_id}/content.md",
            Body=b"# Hello\n",
            ContentType="text/markdown",
        )


class TestDownloadContent:
    @pytest.mark.asyncio
    async def test_downloads_from_correct_key(
        self, repo: s3_repository.GuidanceS3Repository, mock_s3: MagicMock
    ) -> None:
        doc_id = uuid.UUID("507f1f77-bcf8-6cd7-9943-9011aabbccdd")
        body_mock = MagicMock()
        body_mock.read.return_value = b"# My Guidance\n"
        mock_s3.get_object.return_value = {"Body": body_mock}

        result = await repo.download_content(doc_id)

        mock_s3.get_object.assert_called_once_with(
            Bucket="guidance-bucket",
            Key=f"parsed_guidance/{doc_id}/content.md",
        )
        assert result == "# My Guidance\n"

    @pytest.mark.asyncio
    async def test_returns_decoded_string(
        self, repo: s3_repository.GuidanceS3Repository, mock_s3: MagicMock
    ) -> None:
        doc_id = uuid.UUID("507f1f77-bcf8-6cd7-9943-9011aabbccdd")
        body_mock = MagicMock()
        body_mock.read.return_value = "# Title\n\nContent with unicode: café".encode()
        mock_s3.get_object.return_value = {"Body": body_mock}

        result = await repo.download_content(doc_id)

        assert result == "# Title\n\nContent with unicode: café"
