"""Tests for the section summariser's grounding of entries in the manifest."""

import dataclasses
from typing import Any, cast

import pydantic_ai
import pytest

from app.guidance.summaries import models
from app.guidance.summaries.agents import section_summariser

SECTIONS = [("1", "Introduction"), ("2", "Assign case"), ("2.1", "Check the claim")]


@dataclasses.dataclass
class StubRunContext:
    deps: models.SectionSummaryDependencies
    last_attempt: bool = False


def _context(last_attempt: bool = False) -> Any:
    return StubRunContext(
        deps=models.SectionSummaryDependencies(
            document_title="A Guide",
            document_markdown="# A Guide",
            sections=SECTIONS,
        ),
        last_attempt=last_attempt,
    )


def _output(*numbers: str) -> models.SectionSummariesOutput:
    return models.SectionSummariesOutput(
        sections=[
            models.SectionSummaryOutput(number=number, summary=f"About {number}.")
            for number in numbers
        ]
    )


async def _validate(
    output: models.SectionSummariesOutput, last_attempt: bool = False
) -> models.SectionSummariesOutput:
    return await section_summariser.validate_sections_are_the_documents_own(
        cast(
            pydantic_ai.RunContext[models.SectionSummaryDependencies],
            _context(last_attempt),
        ),
        output,
    )


class TestValidateSections:
    async def test_accepts_an_entry_for_every_section(self) -> None:
        result = await _validate(_output("1", "2", "2.1"))

        assert [entry.number for entry in result.sections] == ["1", "2", "2.1"]

    async def test_drops_a_section_the_document_does_not_have(self) -> None:
        result = await _validate(_output("1", "2", "2.1", "99"), last_attempt=True)

        assert [entry.number for entry in result.sections] == ["1", "2", "2.1"]

    async def test_asks_again_for_a_section_left_out(self) -> None:
        with pytest.raises(pydantic_ai.ModelRetry) as err:
            await _validate(_output("1"))

        assert "2, 2.1" in str(err.value)

    async def test_takes_what_it_has_on_the_final_attempt(self) -> None:
        result = await _validate(_output("1"), last_attempt=True)

        assert [entry.number for entry in result.sections] == ["1"]
