"""Tests for the aggregator agent's instruction builder."""

import dataclasses

from app.publishing import models
from app.publishing.agents import aggregator


class FakePromptRepository:
    """Returns a fixed body and records the names it was asked for."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.requested: list[str] = []

    async def get_prompt_by_name(self, name: str) -> str:
        self.requested.append(name)
        return self.body


@dataclasses.dataclass
class StubRunContext:
    deps: models.AggregatorDependencies


class TestAggregatorInstructions:
    """The aggregator agent's instruction builder."""

    async def test_instructions_serialize_sections_and_verdict(self) -> None:
        """Instructions carry the prompt, the fixed verdict, and every section."""
        repo = FakePromptRepository("AGGREGATOR PROMPT")
        deps = models.AggregatorDependencies(
            section_summaries=[
                models.SectionSummary(
                    section_number="1",
                    summary="Section one is fine.",
                    verdict=models.ReadinessVerdict.READY,
                ),
                models.SectionSummary(
                    section_number="2",
                    summary="Section two has issues.",
                    verdict=models.ReadinessVerdict.NOT_READY,
                ),
            ],
            overall_verdict=models.ReadinessVerdict.NOT_READY,
            prompt_repository=repo,
        )

        result = await aggregator.get_instructions(StubRunContext(deps=deps))

        assert repo.requested == ["aggregator.md"]
        assert result.startswith("AGGREGATOR PROMPT")
        assert "OVERALL VERDICT (already decided, do not contradict it): not_ready" in (
            result
        )
        assert "Section 1 (verdict: ready):\nSection one is fine." in result
        assert "Section 2 (verdict: not_ready):\nSection two has issues." in result
