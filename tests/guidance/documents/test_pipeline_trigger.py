"""Tests for PipelineExecutor status transitions."""

import uuid
from unittest.mock import AsyncMock

from app.guidance.documents import models, parser, pipeline_trigger


def _make_document() -> models.GuidanceDocument:
    return models.GuidanceDocument(
        id=uuid.uuid4(),
        status=models.ExtractionStatus.PROCESSING,
        path="s3://guidance-bucket/original_docs/test.docx",
    )


def _make_executor() -> tuple[pipeline_trigger.PipelineExecutor, AsyncMock, AsyncMock]:
    mock_parser: AsyncMock = AsyncMock(spec=parser.DocumentParser)
    mock_repo: AsyncMock = AsyncMock()
    executor = pipeline_trigger.PipelineExecutor(mock_parser, mock_repo)
    return executor, mock_parser, mock_repo


class TestPipelineExecutor:
    async def test_persists_complete_status_on_success(self) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        document = _make_document()

        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE,
            content="# Parsed content",
        )

        await executor.execute(document)

        mock_repo.update_document.assert_called_once_with(document)
        assert document.status == models.ExtractionStatus.COMPLETE
        assert document.content == "# Parsed content"
        assert document.error_message is None

    async def test_persists_failed_status_on_parse_failure(self) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        document = _make_document()

        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.FAILED,
            error_message="S3 key not found",
        )

        await executor.execute(document)

        mock_repo.update_document.assert_called_once_with(document)
        assert document.status == models.ExtractionStatus.FAILED
        assert document.error_message == "S3 key not found"
        assert document.content is None

    async def test_infers_title_when_parser_returns_one(self) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        document = _make_document()
        document.title = None

        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE,
            content="# My Guide\n\nContent.",
            title="My Guide",
        )

        await executor.execute(document)

        assert document.title == "My Guide"

    async def test_does_not_overwrite_title_when_parser_returns_none(self) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        document = _make_document()
        document.title = "Original Title"

        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE,
            content="# Content",
            title=None,
        )

        await executor.execute(document)

        assert document.title == "Original Title"
