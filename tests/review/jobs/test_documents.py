"""Tests for GuidanceDocumentContentSource (review jobs)."""

import uuid
from unittest.mock import AsyncMock

from app.guidance.documents import models as guidance_models
from app.review.jobs import documents


def _make_guidance_doc(
    status: guidance_models.ExtractionStatus = guidance_models.ExtractionStatus.COMPLETE,
    title: str | None = "My Guidance",
) -> guidance_models.GuidanceDocument:
    return guidance_models.GuidanceDocument(
        id=uuid.uuid4(),
        title=title,
        status=status,
    )


def _make_source(
    doc: guidance_models.GuidanceDocument | None = None,
    markdown: str = "# Content\n",
) -> documents.GuidanceDocumentContentSource:
    guidance_repo = AsyncMock()
    guidance_repo.get_document = AsyncMock(return_value=doc)

    storage_repo = AsyncMock()
    storage_repo.download_content = AsyncMock(return_value=markdown)

    return documents.GuidanceDocumentContentSource(guidance_repo, storage_repo)


class TestGuidanceDocumentContentSource:
    async def test_returns_none_when_document_missing(self) -> None:
        source = _make_source(doc=None)

        result = await source.get(uuid.uuid4())

        assert result is None

    async def test_returns_not_ready_for_pending_document(self) -> None:
        doc = _make_guidance_doc(status=guidance_models.ExtractionStatus.PENDING)
        source = _make_source(doc=doc)

        result = await source.get(doc.id)

        assert result is not None
        assert result.ready is False

    async def test_does_not_fetch_s3_for_non_complete_document(self) -> None:
        doc = _make_guidance_doc(status=guidance_models.ExtractionStatus.PENDING)
        storage_repo = AsyncMock()
        guidance_repo = AsyncMock()
        guidance_repo.get_document = AsyncMock(return_value=doc)
        source = documents.GuidanceDocumentContentSource(guidance_repo, storage_repo)

        await source.get(doc.id)

        storage_repo.download_content.assert_not_called()

    async def test_returns_ready_with_markdown_for_complete_document(self) -> None:
        doc = _make_guidance_doc(status=guidance_models.ExtractionStatus.COMPLETE)
        source = _make_source(doc=doc, markdown="# My Guidance\n\nContent here.")

        result = await source.get(doc.id)

        assert result is not None
        assert result.ready is True
        assert result.content == "# My Guidance\n\nContent here."

    async def test_passes_document_id_to_storage_repo(self) -> None:
        doc = _make_guidance_doc(status=guidance_models.ExtractionStatus.COMPLETE)
        storage_repo = AsyncMock()
        storage_repo.download_content = AsyncMock(return_value="# Content")
        guidance_repo = AsyncMock()
        guidance_repo.get_document = AsyncMock(return_value=doc)
        source = documents.GuidanceDocumentContentSource(guidance_repo, storage_repo)

        await source.get(doc.id)

        storage_repo.download_content.assert_called_once_with(doc.id)

    async def test_preserves_document_title(self) -> None:
        doc = _make_guidance_doc(
            status=guidance_models.ExtractionStatus.COMPLETE, title="My Title"
        )
        source = _make_source(doc=doc)

        result = await source.get(doc.id)

        assert result is not None
        assert result.title == "My Title"
