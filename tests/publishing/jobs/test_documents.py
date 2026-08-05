"""Tests for GuidanceDocumentContentSource."""

import uuid
from unittest.mock import AsyncMock

from app.guidance.documents import models as guidance_models
from app.publishing.jobs import documents

_MANIFEST_JSON = (
    '{"document_id": "12345678-1234-5678-1234-567812345678", "title": "My Guidance", '
    '"sections": ['
    '{"number": "1", "heading": "One", "level": 1, "parent": null, "children": ["1.1"], "links": []},'
    '{"number": "1.1", "heading": "Sub", "level": 2, "parent": "1", "children": [], "links": []},'
    '{"number": "2", "heading": "Two", "level": 1, "parent": null, "children": [], "links": []}'
    "]}"
)

_SECTION_MARKDOWN = {
    "1": "## 1 One\n\nA.",
    "1.1": "### 1.1 Sub\n\nB.",
    "2": "## 2 Two\n\nC.",
}


def _make_guidance_doc(
    status: guidance_models.ExtractionStatus = guidance_models.ExtractionStatus.COMPLETE,
    title: str | None = "My Guidance",
) -> guidance_models.GuidanceDocument:
    return guidance_models.GuidanceDocument(
        id=uuid.uuid4(),
        title=title,
        status=status,
    )


def _make_storage_repo() -> AsyncMock:
    storage_repo = AsyncMock()
    storage_repo.download_manifest = AsyncMock(return_value=_MANIFEST_JSON)
    storage_repo.download_section = AsyncMock(
        side_effect=lambda _doc_id, number: _SECTION_MARKDOWN[number]
    )
    return storage_repo


def _make_source(
    doc: guidance_models.GuidanceDocument | None = None,
) -> documents.GuidanceDocumentContentSource:
    guidance_repo = AsyncMock()
    guidance_repo.get_document = AsyncMock(return_value=doc)

    return documents.GuidanceDocumentContentSource(guidance_repo, _make_storage_repo())


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
        assert result.sections == []

    async def test_returns_not_ready_for_processing_document(self) -> None:
        doc = _make_guidance_doc(status=guidance_models.ExtractionStatus.PROCESSING)
        source = _make_source(doc=doc)

        result = await source.get(doc.id)

        assert result is not None
        assert result.ready is False

    async def test_returns_not_ready_for_failed_document(self) -> None:
        doc = _make_guidance_doc(status=guidance_models.ExtractionStatus.FAILED)
        source = _make_source(doc=doc)

        result = await source.get(doc.id)

        assert result is not None
        assert result.ready is False

    async def test_does_not_fetch_s3_for_non_complete_document(self) -> None:
        doc = _make_guidance_doc(status=guidance_models.ExtractionStatus.PENDING)
        storage_repo = _make_storage_repo()
        guidance_repo = AsyncMock()
        guidance_repo.get_document = AsyncMock(return_value=doc)
        source = documents.GuidanceDocumentContentSource(guidance_repo, storage_repo)

        await source.get(doc.id)

        storage_repo.download_manifest.assert_not_called()
        storage_repo.download_section.assert_not_called()

    async def test_returns_one_section_per_top_level_number(self) -> None:
        doc = _make_guidance_doc()
        source = _make_source(doc=doc)

        result = await source.get(doc.id)

        assert result is not None
        assert result.ready is True
        assert [section.number for section in result.sections] == ["1", "2"]

    async def test_section_text_includes_descendants(self) -> None:
        doc = _make_guidance_doc()
        source = _make_source(doc=doc)

        result = await source.get(doc.id)

        assert result is not None
        assert result.sections[0].text == "## 1 One\n\nA.\n\n### 1.1 Sub\n\nB.\n"
        assert result.sections[1].text == "## 2 Two\n\nC.\n"

    async def test_downloads_manifest_once(self) -> None:
        doc = _make_guidance_doc()
        storage_repo = _make_storage_repo()
        guidance_repo = AsyncMock()
        guidance_repo.get_document = AsyncMock(return_value=doc)
        source = documents.GuidanceDocumentContentSource(guidance_repo, storage_repo)

        await source.get(doc.id)

        storage_repo.download_manifest.assert_called_once_with(doc.id)

    async def test_preserves_document_title(self) -> None:
        doc = _make_guidance_doc(
            status=guidance_models.ExtractionStatus.COMPLETE, title="My Title"
        )
        source = _make_source(doc=doc)

        result = await source.get(doc.id)

        assert result is not None
        assert result.title == "My Title"
