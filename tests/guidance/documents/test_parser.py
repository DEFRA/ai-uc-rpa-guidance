"""Tests for PipelineDocumentParser."""

import io
from unittest.mock import AsyncMock

import bson
import docx
import pytest

from app.guidance.documents import models
from app.guidance.documents.parser import ParseResult, PipelineDocumentParser
from app.guidance.documents.s3_repository import GuidanceS3Repository


def _make_minimal_docx(title: str = "") -> bytes:
    """Build a minimal in-memory .docx with a heading and a paragraph."""
    doc = docx.Document()
    if title:
        doc.core_properties.title = title
    doc.add_heading("Test Section", level=1)
    doc.add_paragraph("Hello world.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_document(
    path: str = "s3://source-bucket/doc-id/file-id",
) -> models.GuidanceDocument:
    return models.GuidanceDocument(
        id=str(bson.ObjectId()),
        title="Test Doc",
        status=models.ExtractionStatus.PROCESSING,
        path=path,
    )


@pytest.fixture
def s3_repo() -> AsyncMock:
    repo = AsyncMock(spec=GuidanceS3Repository)
    repo.download_docx.return_value = _make_minimal_docx()
    return repo


@pytest.fixture
def parser_instance(s3_repo: AsyncMock) -> PipelineDocumentParser:
    return PipelineDocumentParser(s3_repo)


class TestPipelineDocumentParserSuccess:
    @pytest.mark.asyncio
    async def test_returns_complete_status(
        self,
        parser_instance: PipelineDocumentParser,
    ) -> None:
        result = await parser_instance.parse(_make_document())

        assert result.status == models.ExtractionStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_content_populated(
        self,
        parser_instance: PipelineDocumentParser,
    ) -> None:
        result = await parser_instance.parse(_make_document())

        assert result.content is not None
        assert len(result.content) > 0
        assert "Test Section" in result.content

    @pytest.mark.asyncio
    async def test_error_message_none_on_success(
        self,
        parser_instance: PipelineDocumentParser,
    ) -> None:
        result = await parser_instance.parse(_make_document())

        assert result.error_message is None

    @pytest.mark.asyncio
    async def test_markdown_uploaded_to_s3(
        self,
        parser_instance: PipelineDocumentParser,
        s3_repo: AsyncMock,
    ) -> None:
        document = _make_document()
        result = await parser_instance.parse(document)

        s3_repo.upload_content.assert_called_once_with(document.id, result.content)

    @pytest.mark.asyncio
    async def test_docx_downloaded_with_correct_key(
        self,
        parser_instance: PipelineDocumentParser,
        s3_repo: AsyncMock,
    ) -> None:
        await parser_instance.parse(
            _make_document(path="s3://source-bucket/doc-id/file-id")
        )

        s3_repo.download_docx.assert_called_once_with("doc-id/file-id")

    @pytest.mark.asyncio
    async def test_title_inferred_from_docx_when_not_set(
        self,
        s3_repo: AsyncMock,
    ) -> None:
        s3_repo.download_docx.return_value = _make_minimal_docx(title="Inferred Title")
        parser_inst = PipelineDocumentParser(s3_repo)
        document = models.GuidanceDocument(
            id=str(bson.ObjectId()),
            title=None,
            status=models.ExtractionStatus.PROCESSING,
            path="s3://source-bucket/doc-id/file-id",
        )
        result = await parser_inst.parse(document)

        assert result.title == "Inferred Title"

    @pytest.mark.asyncio
    async def test_title_none_when_already_set_on_document(
        self,
        s3_repo: AsyncMock,
    ) -> None:
        s3_repo.download_docx.return_value = _make_minimal_docx(title="Inferred Title")
        parser_inst = PipelineDocumentParser(s3_repo)
        document = models.GuidanceDocument(
            id=str(bson.ObjectId()),
            title="Explicit Title",
            status=models.ExtractionStatus.PROCESSING,
            path="s3://source-bucket/doc-id/file-id",
        )
        result = await parser_inst.parse(document)

        assert result.title is None


class TestPipelineDocumentParserFailure:
    @pytest.mark.asyncio
    async def test_s3_error_returns_failed_status(
        self,
        parser_instance: PipelineDocumentParser,
        s3_repo: AsyncMock,
    ) -> None:
        s3_repo.download_docx.side_effect = Exception("S3 connection error")

        result = await parser_instance.parse(_make_document())

        assert result.status == models.ExtractionStatus.FAILED
        assert "S3 connection error" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_missing_path_returns_failed_status(
        self,
        parser_instance: PipelineDocumentParser,
    ) -> None:
        document = _make_document(path="")
        document.path = None

        result = await parser_instance.parse(document)

        assert result.status == models.ExtractionStatus.FAILED
        assert "has no path set" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_upload_error_returns_failed_status(
        self,
        parser_instance: PipelineDocumentParser,
        s3_repo: AsyncMock,
    ) -> None:
        s3_repo.upload_content.side_effect = Exception("Upload failed")

        result = await parser_instance.parse(_make_document())

        assert result.status == models.ExtractionStatus.FAILED
        assert "Upload failed" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(
        self,
        parser_instance: PipelineDocumentParser,
        s3_repo: AsyncMock,
    ) -> None:
        s3_repo.download_docx.side_effect = Exception("boom")

        result = await parser_instance.parse(_make_document())

        assert isinstance(result, ParseResult)
