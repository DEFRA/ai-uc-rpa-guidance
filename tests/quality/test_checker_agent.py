"""Tests for the quality checker agent: its output schema, prompt and quote validator."""

import dataclasses
import pathlib

import pydantic_ai
import pytest

from app.infra.prompts import repository as prompt_repo
from app.quality import anchors, models
from app.quality.agents import checker
from tests.infra.prompts import fakes

PART = anchors.top_level_parts(
    "# Guide\n\n"
    "## 2 Assign case\n\n"
    "Complete a review of CRM to confirm is any correspondence has been received.\n\n"
    "Assign the case to yourself.\n"
)[0]

PROMPT = pathlib.Path("app/quality/prompts/checker.md").read_text()


@dataclasses.dataclass
class StubRunContext:
    deps: models.AgentDependencies
    last_attempt: bool = False


def make_finding(exact: str) -> models.QualityFinding:
    return models.QualityFinding(
        rule_id=models.RuleId.GM_QUAL_24,
        exact=exact,
        issue="'is' should be 'if'.",
        why_it_matters="The error makes the instruction harder to read.",
        recommendation="Change 'is' to 'if'.",
        confidence=models.ConfidenceLevel.HIGH,
    )


def make_output(*exacts: str) -> models.CheckerOutput:
    return models.CheckerOutput(findings=[make_finding(exact) for exact in exacts])


class TestOutputSchema:
    def test_a_finding_has_the_fields_a_comment_needs_from_the_model(self) -> None:
        assert list(models.QualityFinding.model_fields) == [
            "rule_id",
            "exact",
            "issue",
            "why_it_matters",
            "recommendation",
            "confidence",
        ]

    def test_every_field_is_described_to_the_model(self) -> None:
        fields = models.QualityFinding.model_fields

        assert all(field.description for field in fields.values())

    def test_the_rules_are_those_a_single_section_can_show(self) -> None:
        assert [rule.value for rule in models.RuleId] == [
            "GM-Qual-01",
            "GM-Qual-02",
            "GM-Qual-04",
            "GM-Qual-05",
            "GM-Qual-06",
            "GM-Qual-07",
            "GM-Qual-08",
            "GM-Qual-09",
            "GM-Qual-10",
            "GM-Qual-11",
            "GM-Qual-12",
            "GM-Qual-13",
            "GM-Qual-14",
            "GM-Qual-17",
            "GM-Qual-18",
            "GM-Qual-20",
            "GM-Qual-23",
            "GM-Qual-24",
            "GM-Qual-25",
            "GM-Qual-26",
            "GM-Qual-28",
        ]

    def test_every_rule_is_at_version_1(self) -> None:
        assert set(models.RULE_VERSIONS.values()) == {1}
        assert set(models.RULE_VERSIONS) == set(models.RuleId)


class TestPrompt:
    def test_the_prompt_states_every_rule(self) -> None:
        missing = [rule.value for rule in models.RuleId if rule.value not in PROMPT]

        assert missing == []

    def test_the_prompt_states_no_rule_that_needs_the_whole_document(self) -> None:
        whole_document_rules = [
            "GM-Qual-03",
            "GM-Qual-15",
            "GM-Qual-16",
            "GM-Qual-19",
            "GM-Qual-21",
            "GM-Qual-22",
        ]

        assert [rule for rule in whole_document_rules if rule in PROMPT] == []


class TestInstructions:
    async def test_the_section_follows_the_rules(self) -> None:
        fs = fakes.FakeFileSystem()
        fs.set_file("checker.md", "The rules.")
        deps = models.AgentDependencies(
            part=PART,
            prompt_repository=prompt_repo.FileSystemPromptRepository("prompts", fs),
        )

        instructions = await checker.get_instructions(StubRunContext(deps=deps))

        assert instructions == f"The rules.\n\n{PART.markdown}"


class TestQuoteValidator:
    async def test_a_verbatim_quote_found_once_passes(self) -> None:
        output = make_output("confirm is any correspondence")

        result = await checker.validate_quotes_anchor(
            StubRunContext(deps=models.AgentDependencies(part=PART)), output
        )

        assert result is output

    async def test_a_quote_not_in_the_section_is_sent_back(self) -> None:
        output = make_output("confirm if any correspondence")
        ctx = StubRunContext(deps=models.AgentDependencies(part=PART))

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await checker.validate_quotes_anchor(ctx, output)

        assert "confirm if any correspondence" in str(exc_info.value)
        assert "not found" in str(exc_info.value)

    async def test_a_quote_found_more_than_once_is_sent_back(self) -> None:
        output = make_output("case")
        ctx = StubRunContext(deps=models.AgentDependencies(part=PART))

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await checker.validate_quotes_anchor(ctx, output)

        assert "'case'" in str(exc_info.value)
        assert "more than once" in str(exc_info.value)

    async def test_on_the_last_attempt_unanchored_findings_are_dropped(self) -> None:
        output = make_output("confirm is any correspondence", "not in the section")
        ctx = StubRunContext(
            deps=models.AgentDependencies(part=PART), last_attempt=True
        )

        result = await checker.validate_quotes_anchor(ctx, output)

        assert [finding.exact for finding in result.findings] == [
            "confirm is any correspondence"
        ]
