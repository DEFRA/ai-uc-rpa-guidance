"""Tests for the guidance document summary service."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.guidance.documents import models as document_models
from app.guidance.documents import repository as document_repository
from app.guidance.documents import s3_repository
from app.guidance.summaries import models, repository, service

DOCUMENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _make_document(
    document_id: uuid.UUID = DOCUMENT_ID,
    status: document_models.ExtractionStatus = document_models.ExtractionStatus.COMPLETE,
    title: str | None = None,
    filename: str | None = "guide.docx",
) -> document_models.GuidanceDocument:
    return document_models.GuidanceDocument(
        id=document_id,
        title=title,
        filename=filename,
        status=status,
    )


def _make_manifest(
    title: str = "The Real Title", sections: list[str] | None = None
) -> str:
    return json.dumps(
        {
            "documentId": str(DOCUMENT_ID),
            "title": title,
            "sections": [
                {"number": number, "heading": f"Heading {number}", "level": 1}
                for number in (["1", "2"] if sections is None else sections)
            ],
        }
    )


def _make_summary(
    document_id: uuid.UUID = DOCUMENT_ID, title: str = "A Guide"
) -> models.DocumentSummary:
    return models.DocumentSummary(
        document_id=document_id,
        title=title,
        about="What it is about.",
        used_for="What it is used for.",
        path=f"s3://bucket/parsed_guidance/{document_id}/summary.md",
        keywords=["ROCR"],
        acronyms=[],
        start_path=f"/guidance-documents/{document_id}/sections/1",
        model="anthropic.claude-sonnet-4-6",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _section_result(
    *numbers: str, acronyms: dict[str, list[models.AcronymOutput]] | None = None
) -> Mock:
    result = Mock()
    result.output = models.SectionSummariesOutput(
        sections=[
            models.SectionSummaryOutput(
                number=number,
                summary=f"What section {number} covers.",
                keywords=[f"term-{number}"],
                acronyms=(acronyms or {}).get(number, []),
            )
            for number in numbers
        ]
    )
    return result


def _make_section(
    document_id: uuid.UUID, number: str, order: int
) -> models.SectionSummary:
    return models.SectionSummary(
        document_id=document_id,
        number=number,
        heading=f"Heading {number}",
        level=1,
        summary=f"What section {number} covers.",
        keywords=[],
        acronyms=[],
        start_path=f"/guidance-documents/{document_id}/sections/{number}",
        order=order,
    )


def _agent_result(
    about: str = "What it is about.",
    used_for: str = "What it is used for.",
    keywords: list[str] | None = None,
) -> Mock:
    result = Mock()
    result.output = models.SummaryOutput(
        about=about,
        used_for=used_for,
        keywords=["ROCR"] if keywords is None else keywords,
    )
    return result


def _agents(*numbers: str) -> Any:
    """Patch the section pass, which every rebuild runs after the document pass."""
    return patch(
        "app.guidance.summaries.service.section_summariser.section_summariser_agent.run",
        new_callable=AsyncMock,
        return_value=_section_result(*(numbers or ("1", "2"))),
    )


@pytest.fixture
def documents() -> AsyncMock:
    repo = AsyncMock(spec=document_repository.GuidanceRepository)
    repo.get_document.return_value = _make_document()
    return repo


@pytest.fixture
def summaries() -> AsyncMock:
    repo = AsyncMock(spec=repository.SummaryRepository)
    repo.save_summary.side_effect = lambda summary: summary
    repo.delete_all_summaries.return_value = 0
    return repo


@pytest.fixture
def storage() -> AsyncMock:
    repo = AsyncMock(spec=s3_repository.AbstractGuidanceStorageRepository)
    repo.download_content.return_value = "# A guide\n\nSome guidance."
    repo.download_manifest.return_value = _make_manifest()
    repo.upload_summary.return_value = (
        f"s3://bucket/parsed_guidance/{DOCUMENT_ID}/summary.md"
    )
    repo.delete_summaries.return_value = 0
    return repo


@pytest.fixture
def sections() -> AsyncMock:
    repo = AsyncMock(spec=repository.SectionSummaryRepository)
    repo.save_sections.side_effect = lambda entries: entries
    repo.delete_all_sections.return_value = 0
    repo.list_sections.return_value = []
    return repo


@pytest.fixture
def summary_service(
    documents: AsyncMock,
    summaries: AsyncMock,
    sections: AsyncMock,
    storage: AsyncMock,
) -> service.SummaryService:
    return service.SummaryService(documents, summaries, sections, storage)


class TestRebuild:
    async def test_summarises_the_parsed_markdown(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ) as run,
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.failures == []
        assert len(response.items) == 1
        assert response.items[0].about == "What it is about."
        assert response.items[0].used_for == "What it is used for."
        assert run.await_args.kwargs["deps"].document_markdown == (
            storage.download_content.return_value
        )

    async def test_stores_the_rendered_markdown_beside_the_parse_outputs(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.return_value = _make_manifest(sections=["1"])

        with (
            _agents("1"),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            await summary_service.rebuild([DOCUMENT_ID])

        document_id, markdown = storage.upload_summary.await_args.args
        assert document_id == DOCUMENT_ID
        assert markdown == (
            "# The Real Title\n\n"
            f"[Open this document](/guidance-documents/{DOCUMENT_ID}/sections/1)\n\n"
            "## What this document is about\n\n"
            "What it is about.\n\n"
            "## What it is used for\n\n"
            "What it is used for.\n\n"
            "## Terms\n\n"
            "ROCR\n\n"
            "## Sections\n\n"
            "### 1 Heading 1\n\n"
            f"[Open this section](/guidance-documents/{DOCUMENT_ID}/sections/1)\n\n"
            "What section 1 covers.\n\n"
            "Terms: term-1\n"
        )

    async def test_the_stored_markdown_spells_out_a_sections_acronyms(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.return_value = _make_manifest(sections=["1"])
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                acronyms={
                    "1": [
                        models.AcronymOutput(
                            acronym="SDA", expansion="Severely Disadvantaged Area"
                        ),
                        models.AcronymOutput(acronym="IAPA", expansion=None),
                    ]
                },
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            await summary_service.rebuild([DOCUMENT_ID])

        _, markdown = storage.upload_summary.await_args.args
        assert "Acronyms: SDA (Severely Disadvantaged Area), IAPA" in markdown

    async def test_records_the_summary_against_the_document(
        self, summary_service: service.SummaryService, summaries: AsyncMock
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            await summary_service.rebuild([DOCUMENT_ID])

        saved = summaries.save_summary.await_args.args[0]
        assert saved.document_id == DOCUMENT_ID
        assert saved.path == f"s3://bucket/parsed_guidance/{DOCUMENT_ID}/summary.md"
        assert saved.model

    async def test_links_the_summary_to_the_documents_first_section(
        self, summary_service: service.SummaryService
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].start_path == (
            f"/guidance-documents/{DOCUMENT_ID}/sections/1"
        )

    async def test_links_to_the_first_section_the_parse_numbered(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.return_value = _make_manifest(sections=["2", "3"])

        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].start_path == (
            f"/guidance-documents/{DOCUMENT_ID}/sections/2"
        )

    async def test_links_to_the_contents_page_without_sections(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.return_value = _make_manifest(sections=[])

        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].start_path == f"/guidance-documents/{DOCUMENT_ID}/view"

    async def test_links_to_the_contents_page_without_a_manifest(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.side_effect = Exception("NoSuchKey")

        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].start_path == f"/guidance-documents/{DOCUMENT_ID}/view"

    async def test_titles_the_summary_from_the_manifest(
        self, summary_service: service.SummaryService
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].title == "The Real Title"

    async def test_falls_back_to_the_filename_without_a_manifest(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.side_effect = Exception("NoSuchKey")

        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].title == "guide.docx"

    async def test_reports_an_unknown_document_as_a_failure(
        self, summary_service: service.SummaryService, documents: AsyncMock
    ) -> None:
        documents.get_document.return_value = None

        response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items == []
        assert len(response.failures) == 1
        assert response.failures[0].document_id == str(DOCUMENT_ID)
        assert "not found" in response.failures[0].error_message

    async def test_reports_an_unparsed_document_as_a_failure(
        self, summary_service: service.SummaryService, documents: AsyncMock
    ) -> None:
        documents.get_document.return_value = _make_document(
            status=document_models.ExtractionStatus.PROCESSING
        )

        response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items == []
        assert "has not been parsed" in response.failures[0].error_message

    async def test_one_failure_does_not_sink_the_rest(
        self, summary_service: service.SummaryService, documents: AsyncMock
    ) -> None:
        other_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        documents.get_document.side_effect = lambda document_id: (
            None if document_id == DOCUMENT_ID else _make_document(document_id)
        )

        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID, other_id])

        assert [item.document_id for item in response.items] == [str(other_id)]
        assert [failure.document_id for failure in response.failures] == [
            str(DOCUMENT_ID)
        ]

    async def test_summarises_nothing_when_nothing_is_selected(
        self, summary_service: service.SummaryService, documents: AsyncMock
    ) -> None:
        response = await summary_service.rebuild([])

        assert response.items == []
        assert response.failures == []
        documents.get_document.assert_not_awaited()


class TestSectionEntries:
    async def test_indexes_every_section_the_manifest_names(
        self, summary_service: service.SummaryService, sections: AsyncMock
    ) -> None:
        with (
            _agents("1", "2"),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        entries = response.items[0].sections
        assert [entry.number for entry in entries] == ["1", "2"]
        assert entries[0].heading == "Heading 1"
        assert entries[0].summary == "What section 1 covers."
        assert entries[0].keywords == ["term-1"]
        sections.save_sections.assert_awaited_once()

    async def test_deep_links_each_section_into_the_viewer(
        self, summary_service: service.SummaryService
    ) -> None:
        with (
            _agents("1", "2"),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert [entry.start_path for entry in response.items[0].sections] == [
            f"/guidance-documents/{DOCUMENT_ID}/sections/1",
            f"/guidance-documents/{DOCUMENT_ID}/sections/2",
        ]

    async def test_keeps_the_manifest_order_whatever_order_the_model_answers_in(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.return_value = _make_manifest(
            sections=["1", "1.9", "1.10"]
        )

        with (
            _agents("1.10", "1", "1.9"),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert [entry.number for entry in response.items[0].sections] == [
            "1",
            "1.9",
            "1.10",
        ]

    async def test_leaves_out_a_section_the_model_did_not_summarise(
        self, summary_service: service.SummaryService
    ) -> None:
        with (
            _agents("1"),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert [entry.number for entry in response.items[0].sections] == ["1"]

    async def test_indexes_no_sections_without_a_manifest(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.side_effect = Exception("NoSuchKey")

        with (
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
            patch(
                "app.guidance.summaries.service.section_summariser."
                "section_summariser_agent.run",
                new_callable=AsyncMock,
            ) as section_run,
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].sections == []
        section_run.assert_not_awaited()


class TestTermsAndAcronyms:
    async def test_records_the_terms_the_document_would_be_searched_by(
        self, summary_service: service.SummaryService
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(keywords=["LULC", "Land Use or Land Cover"]),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].keywords == ["LULC", "Land Use or Land Cover"]

    async def test_indexes_the_acronyms_its_sections_use(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                "2",
                acronyms={
                    "1": [models.AcronymOutput(acronym="SDA", expansion=None)],
                    "2": [
                        models.AcronymOutput(
                            acronym="SDA", expansion="Severely Disadvantaged Area"
                        )
                    ],
                },
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        index = response.items[0].acronyms
        assert len(index) == 1
        assert index[0].acronym == "SDA"
        assert index[0].expansion == "Severely Disadvantaged Area"
        assert index[0].sections == ["1", "2"]

    async def test_a_sections_acronyms_are_among_its_own_terms(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                acronyms={
                    "1": [
                        models.AcronymOutput(acronym="SSSI", expansion=None),
                        # Already a keyword: it must not be listed twice.
                        models.AcronymOutput(acronym="term-1", expansion=None),
                    ]
                },
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].sections[0].keywords == ["term-1", "SSSI"]

    async def test_a_section_keeps_its_acronyms_with_their_expansions(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                "2",
                acronyms={
                    "1": [
                        models.AcronymOutput(
                            acronym="SDA", expansion="Severely Disadvantaged Area"
                        )
                    ],
                    "2": [models.AcronymOutput(acronym="IAPA", expansion=None)],
                },
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        first, second = response.items[0].sections
        assert [entry.acronym for entry in first.acronyms] == ["SDA"]
        assert first.acronyms[0].expansion == "Severely Disadvantaged Area"
        assert [entry.acronym for entry in second.acronyms] == ["IAPA"]
        assert second.acronyms[0].expansion is None

    async def test_the_index_names_only_sections_that_carry_the_acronym(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                "2",
                acronyms={"2": [models.AcronymOutput(acronym="SSSI", expansion=None)]},
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        document = response.items[0]
        assert document.acronyms[0].sections == ["2"]
        assert document.sections[0].acronyms == []
        assert [entry.acronym for entry in document.sections[1].acronyms] == ["SSSI"]

    async def test_indexes_no_acronyms_for_a_document_without_sections(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        storage.download_manifest.side_effect = Exception("NoSuchKey")

        with patch(
            "app.guidance.summaries.service.summariser.summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_agent_result(),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items[0].acronyms == []


class TestPurge:
    async def test_discards_the_whole_index_before_rebuilding(
        self, summary_service: service.SummaryService, summaries: AsyncMock
    ) -> None:
        order: list[str] = []
        summaries.delete_all_summaries.side_effect = lambda: order.append("purge") or 0
        summaries.save_summary.side_effect = lambda summary: (
            order.append("save"),
            summary,
        )[1]

        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            await summary_service.rebuild([DOCUMENT_ID])

        assert order == ["purge", "save"]

    async def test_discards_the_section_entries_too(
        self, summary_service: service.SummaryService, sections: AsyncMock
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            await summary_service.rebuild([DOCUMENT_ID])

        sections.delete_all_sections.assert_awaited_once()

    async def test_discards_the_stored_markdown_too(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            await summary_service.rebuild([DOCUMENT_ID])

        storage.delete_summaries.assert_awaited_once()

    async def test_reports_how_much_was_discarded(
        self, summary_service: service.SummaryService, summaries: AsyncMock
    ) -> None:
        summaries.delete_all_summaries.return_value = 7

        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.purged == 7

    async def test_purges_even_when_every_document_fails(
        self, summary_service: service.SummaryService, documents: AsyncMock
    ) -> None:
        documents.get_document.return_value = None

        response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.items == []
        assert len(response.failures) == 1


class TestTiming:
    # time.monotonic is not patched here: it is the module asyncio itself
    # reads, and feeding it fixed values breaks the event loop.
    async def test_reports_how_long_the_rebuild_took(
        self, summary_service: service.SummaryService
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await summary_service.rebuild([DOCUMENT_ID])

        assert response.duration_seconds >= 0

    async def test_times_a_rebuild_that_summarised_nothing(
        self, summary_service: service.SummaryService
    ) -> None:
        response = await summary_service.rebuild([])

        assert response.duration_seconds >= 0


class TestListSummaries:
    async def test_returns_every_stored_summary(
        self, summary_service: service.SummaryService, summaries: AsyncMock
    ) -> None:
        summaries.list_summaries.return_value = [_make_summary()]

        response = await summary_service.list_summaries()

        assert len(response.items) == 1
        assert response.items[0].document_id == str(DOCUMENT_ID)
        assert response.items[0].title == "A Guide"

    async def test_gives_each_document_its_own_sections(
        self,
        summary_service: service.SummaryService,
        summaries: AsyncMock,
        sections: AsyncMock,
    ) -> None:
        other_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        summaries.list_summaries.return_value = [
            _make_summary(),
            _make_summary(document_id=other_id, title="Another Guide"),
        ]
        sections.list_sections.return_value = [
            _make_section(DOCUMENT_ID, "1", order=0),
            _make_section(other_id, "1", order=0),
            _make_section(DOCUMENT_ID, "2", order=1),
        ]

        response = await summary_service.list_summaries()

        first, second = response.items
        assert [entry.number for entry in first.sections] == ["1", "2"]
        assert [entry.number for entry in second.sections] == ["1"]
