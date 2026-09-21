"""Tests for the guidance search service."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.guidance.documents import s3_repository
from app.guidance.search import models, service
from app.guidance.summaries import models as summary_models
from app.guidance.summaries import repository as summary_repository

DOCUMENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")

RANKER = "app.guidance.search.service.ranker.ranker_agent.run"
ASSESSOR = "app.guidance.search.service.assessor.assessor_agent.run"
ANSWERER = "app.guidance.search.service.answerer.answerer_agent.run"


def _summary() -> summary_models.DocumentSummary:
    return summary_models.DocumentSummary(
        document_id=DOCUMENT_ID,
        title="A Guide",
        about="What it is about.",
        used_for="What it is used for.",
        keywords=["ROCR"],
        acronyms=[],
        path="s3://bucket/summary.md",
        start_path=f"/guidance-documents/{DOCUMENT_ID}/sections/1",
        model="anthropic.claude-sonnet-4-6",
    )


def _section(number: str) -> summary_models.SectionSummary:
    return summary_models.SectionSummary(
        document_id=DOCUMENT_ID,
        number=number,
        heading=f"Heading {number}",
        level=1,
        summary=f"What section {number} covers.",
        keywords=[],
        acronyms=[],
        start_path=f"/guidance-documents/{DOCUMENT_ID}/sections/{number}",
        order=0,
    )


def _ranked(*refs: tuple[str, str | None]) -> Mock:
    result = Mock()
    result.output = models.RankedResults(
        results=[
            models.RankedResult(document_id=document_id, section_number=section_number)
            for document_id, section_number in refs
        ]
    )
    return result


def _relevance(relevant: bool, reason: str = "It tells them what to do.") -> Mock:
    result = Mock()
    result.output = models.Relevance(relevant=relevant, reason=reason)
    return result


def _answer(
    answered: bool = True, cited: list[tuple[str, str | None]] | None = None
) -> Mock:
    result = Mock()
    result.output = models.SearchAnswer(
        answered=answered,
        answer="Do this, then that." if answered else "",
        cited=[
            models.Citation(document_id=document_id, section_number=section_number)
            for document_id, section_number in (cited or [])
        ],
    )
    return result


@pytest.fixture
def summaries() -> AsyncMock:
    repo = AsyncMock(spec=summary_repository.SummaryRepository)
    repo.list_summaries.return_value = [_summary()]
    return repo


@pytest.fixture
def sections() -> AsyncMock:
    repo = AsyncMock(spec=summary_repository.SectionSummaryRepository)
    repo.list_sections.return_value = [_section("1"), _section("2")]
    return repo


@pytest.fixture
def storage() -> AsyncMock:
    repo = AsyncMock(spec=s3_repository.AbstractGuidanceStorageRepository)
    repo.download_section.return_value = "## Heading\n\nThe section itself."
    return repo


@pytest.fixture
def search_service(
    summaries: AsyncMock, sections: AsyncMock, storage: AsyncMock
) -> service.SearchService:
    return service.SearchService(summaries, sections, storage)


def _passes(
    ranked: Mock, relevance: Any = None, answer: Mock | None = None
) -> list[Any]:
    return [
        patch(RANKER, new_callable=AsyncMock, return_value=ranked),
        patch(
            ASSESSOR,
            new_callable=AsyncMock,
            **(
                {"side_effect": relevance}
                if isinstance(relevance, list)
                else {"return_value": relevance or _relevance(True)}
            ),
        ),
        patch(ANSWERER, new_callable=AsyncMock, return_value=answer or _answer()),
    ]


async def _search(
    search_service: service.SearchService,
    query: str,
    ranked: Mock,
    relevance: Any = None,
    answer: Mock | None = None,
) -> Any:
    first, second, third = _passes(ranked, relevance, answer)
    with first, second, third:
        return await search_service.search(query)


class TestSearch:
    async def test_returns_the_section_the_ranker_found(
        self, search_service: service.SearchService
    ) -> None:
        response = await _search(
            search_service, "sda", _ranked((str(DOCUMENT_ID), "2"))
        )

        assert len(response.results) == 1
        result = response.results[0]
        assert result.section_number == "2"
        assert result.heading == "Heading 2"
        assert result.document_title == "A Guide"
        assert result.start_path == f"/guidance-documents/{DOCUMENT_ID}/sections/2"

    async def test_reads_the_section_itself_before_returning_it(
        self, search_service: service.SearchService, storage: AsyncMock
    ) -> None:
        response = await _search(
            search_service, "sda", _ranked((str(DOCUMENT_ID), "1"))
        )

        storage.download_section.assert_awaited_once_with(DOCUMENT_ID, "1")
        assert response.results[0].checked is True

    async def test_says_what_the_section_gives_the_operator(
        self, search_service: service.SearchService
    ) -> None:
        response = await _search(
            search_service,
            "sda",
            _ranked((str(DOCUMENT_ID), "1")),
            relevance=_relevance(True, "It gives them the SDA check."),
        )

        assert response.results[0].reason == "It gives them the SDA check."

    async def test_drops_a_section_that_does_not_bear_the_match_out(
        self, search_service: service.SearchService
    ) -> None:
        response = await _search(
            search_service,
            "sda",
            _ranked((str(DOCUMENT_ID), "1"), (str(DOCUMENT_ID), "2")),
            relevance=[_relevance(False), _relevance(True)],
        )

        assert [result.section_number for result in response.results] == ["2"]

    async def test_keeps_a_whole_document_result_unchecked(
        self, search_service: service.SearchService, storage: AsyncMock
    ) -> None:
        response = await _search(
            search_service, "a guide", _ranked((str(DOCUMENT_ID), None))
        )

        assert response.results[0].section_number is None
        assert response.results[0].heading == "A Guide"
        assert response.results[0].checked is False
        storage.download_section.assert_not_awaited()

    async def test_falls_back_to_the_index_where_nothing_was_read(
        self, search_service: service.SearchService, storage: AsyncMock
    ) -> None:
        storage.download_section.side_effect = Exception("NoSuchKey")

        response = await _search(
            search_service, "sda", _ranked((str(DOCUMENT_ID), "1"))
        )

        # The ranker writes no prose, so an unread section is described by the
        # index entry that got it proposed.
        assert response.results[0].reason == "What section 1 covers."

    async def test_keeps_a_section_it_cannot_read_rather_than_losing_it(
        self, search_service: service.SearchService, storage: AsyncMock
    ) -> None:
        storage.download_section.side_effect = Exception("NoSuchKey")

        response = await _search(
            search_service, "sda", _ranked((str(DOCUMENT_ID), "1"))
        )

        assert len(response.results) == 1
        assert response.results[0].checked is False


class TestAnswer:
    async def test_answers_above_the_results(
        self, search_service: service.SearchService
    ) -> None:
        response = await _search(
            search_service,
            "what do I do?",
            _ranked((str(DOCUMENT_ID), "1")),
            answer=_answer(cited=[(str(DOCUMENT_ID), "1")]),
        )

        assert response.answer is not None
        assert response.answer.answer == "Do this, then that."
        assert [result.section_number for result in response.answer.cited] == ["1"]
        assert response.answer.cited[0].start_path.endswith("/sections/1")

    async def test_gives_no_answer_where_the_results_do_not_support_one(
        self, search_service: service.SearchService
    ) -> None:
        response = await _search(
            search_service,
            "sda",
            _ranked((str(DOCUMENT_ID), "1")),
            answer=_answer(answered=False),
        )

        assert response.answer is None
        assert len(response.results) == 1

    async def test_does_not_answer_when_nothing_was_found(
        self, search_service: service.SearchService
    ) -> None:
        with (
            patch(RANKER, new_callable=AsyncMock, return_value=_ranked()),
            patch(ANSWERER, new_callable=AsyncMock) as answerer_run,
        ):
            response = await search_service.search("nothing matches this")

        assert response.results == []
        assert response.answer is None
        answerer_run.assert_not_awaited()


class TestEmptyIndex:
    async def test_searches_nothing_when_nothing_is_indexed(
        self,
        search_service: service.SearchService,
        summaries: AsyncMock,
    ) -> None:
        summaries.list_summaries.return_value = []

        with patch(RANKER, new_callable=AsyncMock) as ranker_run:
            response = await search_service.search("sda")

        assert response.results == []
        assert response.answer is None
        assert response.indexed_documents == 0
        ranker_run.assert_not_awaited()


class TestReporting:
    async def test_reports_what_it_searched_and_how_long_it_took(
        self, search_service: service.SearchService
    ) -> None:
        response = await _search(
            search_service, "sda", _ranked((str(DOCUMENT_ID), "1"))
        )

        assert response.query == "sda"
        assert response.indexed_documents == 1
        assert response.duration_seconds >= 0
