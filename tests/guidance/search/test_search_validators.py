"""Tests for the search agents' grounding in the index and the results."""

import dataclasses
from typing import Any, cast

import pydantic_ai

from app.guidance.search import models
from app.guidance.search.agents import answerer, ranker

DOCUMENT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


@dataclasses.dataclass
class StubRunContext:
    deps: Any
    last_attempt: bool = False


def _rank_context() -> Any:
    return StubRunContext(
        deps=models.RankDependencies(
            query="sda",
            index="## A Guide",
            entries={(DOCUMENT, None), (DOCUMENT, "1"), (DOCUMENT, "2.1")},
        )
    )


def _results(*refs: tuple[str, str | None]) -> models.RankedResults:
    return models.RankedResults(
        results=[
            models.RankedResult(document_id=document_id, section_number=section_number)
            for document_id, section_number in refs
        ]
    )


async def _validate_ranked(output: models.RankedResults) -> models.RankedResults:
    return await ranker.validate_results_exist(
        cast(pydantic_ai.RunContext[models.RankDependencies], _rank_context()), output
    )


class TestValidateResults:
    async def test_keeps_a_section_the_index_holds(self) -> None:
        result = await _validate_ranked(_results((DOCUMENT, "2.1")))

        assert [entry.section_number for entry in result.results] == ["2.1"]

    async def test_keeps_a_whole_document_result(self) -> None:
        result = await _validate_ranked(_results((DOCUMENT, None)))

        assert [entry.section_number for entry in result.results] == [None]

    async def test_drops_a_section_the_document_does_not_have(self) -> None:
        result = await _validate_ranked(_results((DOCUMENT, "1"), (DOCUMENT, "99")))

        assert [entry.section_number for entry in result.results] == ["1"]

    async def test_drops_a_document_the_index_does_not_hold(self) -> None:
        result = await _validate_ranked(_results((OTHER, None)))

        assert result.results == []

    async def test_returns_at_most_ten_results(self) -> None:
        result = await _validate_ranked(_results(*[(DOCUMENT, "1")] * 14))

        assert len(result.results) == ranker.MAX_RESULTS


def _answer_context() -> Any:
    return StubRunContext(
        deps=models.AnswerDependencies(
            query="sda",
            results="- document_id: ...",
            cited={(DOCUMENT, "1")},
        )
    )


async def _validate_answer(output: models.SearchAnswer) -> models.SearchAnswer:
    return await answerer.validate_citations_are_results(
        cast(pydantic_ai.RunContext[models.AnswerDependencies], _answer_context()),
        output,
    )


class TestValidateCitations:
    async def test_keeps_a_citation_that_is_a_result(self) -> None:
        output = await _validate_answer(
            models.SearchAnswer(
                answered=True,
                answer="Do this.",
                cited=[models.Citation(document_id=DOCUMENT, section_number="1")],
            )
        )

        assert len(output.cited) == 1

    async def test_drops_a_citation_the_operator_cannot_see(self) -> None:
        output = await _validate_answer(
            models.SearchAnswer(
                answered=True,
                answer="Do this.",
                cited=[
                    models.Citation(document_id=DOCUMENT, section_number="1"),
                    models.Citation(document_id=DOCUMENT, section_number="9"),
                    models.Citation(document_id=OTHER, section_number=None),
                ],
            )
        )

        assert [citation.section_number for citation in output.cited] == ["1"]
