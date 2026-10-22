"""Tests for PipelineExecutor status transitions."""

import logging
import uuid
from unittest.mock import AsyncMock

import pytest

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


EC = "2026 CS MA Claim  Admin Evidence Check.docx"
FPG = "CS Revenue Claims Processing to Final Payment Guide 2026.docx"


def _make_uploaded(
    filename: str, linked_filenames: list[str]
) -> models.GuidanceDocument:
    return models.GuidanceDocument(
        id=uuid.uuid4(),
        filename=filename,
        status=models.ExtractionStatus.COMPLETE,
        linked_filenames=linked_filenames,
    )


class TestPipelineExecutorLinks:
    async def test_logs_that_the_new_document_refers_to_an_uploaded_one(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        fpg = _make_uploaded(FPG, linked_filenames=[])
        mock_repo.list_other_documents.return_value = [fpg]
        ec = _make_document()
        ec.filename = EC
        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE, linked_filenames=[FPG]
        )

        with caplog.at_level(logging.INFO):
            await executor.execute(ec)

        assert (
            f"Document {ec.id} ({EC!r}) refers to already uploaded document "
            f"{fpg.id} ({FPG!r})"
        ) in caplog.messages

    async def test_logs_that_the_new_document_is_referred_to_by_an_uploaded_one(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        ec = _make_uploaded(EC, linked_filenames=[FPG])
        mock_repo.list_other_documents.return_value = [ec]
        fpg = _make_document()
        fpg.filename = FPG
        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE, linked_filenames=[]
        )

        with caplog.at_level(logging.INFO):
            await executor.execute(fpg)

        assert (
            f"Document {fpg.id} ({FPG!r}) is referred to by already uploaded document "
            f"{ec.id} ({EC!r})"
        ) in caplog.messages

    async def test_logs_both_when_the_two_documents_link_to_each_other(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        ec = _make_uploaded(EC, linked_filenames=[FPG])
        mock_repo.list_other_documents.return_value = [ec]
        fpg = _make_document()
        fpg.filename = FPG
        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE, linked_filenames=[EC]
        )

        with caplog.at_level(logging.INFO):
            await executor.execute(fpg)

        assert [m for m in caplog.messages if "already uploaded document" in m] == [
            (
                f"Document {fpg.id} ({FPG!r}) refers to already uploaded document "
                f"{ec.id} ({EC!r})"
            ),
            (
                f"Document {fpg.id} ({FPG!r}) is referred to by already uploaded "
                f"document {ec.id} ({EC!r})"
            ),
        ]

    async def test_logs_no_link_when_no_uploaded_document_matches(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        mock_repo.list_other_documents.return_value = [
            _make_uploaded("Unrelated.docx", linked_filenames=["Elsewhere.docx"])
        ]
        fpg = _make_document()
        fpg.filename = FPG
        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE, linked_filenames=[EC]
        )

        with caplog.at_level(logging.INFO):
            await executor.execute(fpg)

        assert not [m for m in caplog.messages if "already uploaded document" in m]

    async def test_keeps_the_documents_the_new_one_links_to(self) -> None:
        executor, mock_parser, mock_repo = _make_executor()
        mock_repo.list_other_documents.return_value = []
        document = _make_document()
        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE, linked_filenames=[EC]
        )

        await executor.execute(document)

        mock_repo.update_document.assert_called_once_with(document)
        assert document.linked_filenames == [EC]
