"""Data models for the quality checker agent: what the LLM is asked to produce."""

import os
from dataclasses import dataclass, field
from enum import StrEnum

import pydantic

from app.infra.prompts import repository as prompt_repo
from app.quality import anchors


class RuleId(StrEnum):
    """The quality check rules one top-level section can show on its own.

    Rules that need the whole document (GM-Qual-03, 15, 16, 19 and 22) or a
    look outside it (GM-Qual-21, links work) are not here. There is no
    GM-Qual-27. The rule wording is in prompts/checker.md, which must name
    every rule here and no other.
    """

    GM_QUAL_01 = "GM-Qual-01"
    GM_QUAL_02 = "GM-Qual-02"
    GM_QUAL_04 = "GM-Qual-04"
    GM_QUAL_05 = "GM-Qual-05"
    GM_QUAL_06 = "GM-Qual-06"
    GM_QUAL_07 = "GM-Qual-07"
    GM_QUAL_08 = "GM-Qual-08"
    GM_QUAL_09 = "GM-Qual-09"
    GM_QUAL_10 = "GM-Qual-10"
    GM_QUAL_11 = "GM-Qual-11"
    GM_QUAL_12 = "GM-Qual-12"
    GM_QUAL_13 = "GM-Qual-13"
    GM_QUAL_14 = "GM-Qual-14"
    GM_QUAL_17 = "GM-Qual-17"
    GM_QUAL_18 = "GM-Qual-18"
    GM_QUAL_20 = "GM-Qual-20"
    GM_QUAL_23 = "GM-Qual-23"
    GM_QUAL_24 = "GM-Qual-24"
    GM_QUAL_25 = "GM-Qual-25"
    GM_QUAL_26 = "GM-Qual-26"
    GM_QUAL_28 = "GM-Qual-28"


# Every rule is at its first version until the rules have an owner who
# changes them.
RULE_VERSIONS: dict[RuleId, int] = dict.fromkeys(RuleId, 1)


class ConfidenceLevel(StrEnum):
    """How sure the agent is that a finding is real.

    - HIGH: clear, specific evidence in the text.
    - MODERATE: probable, but could be a conversion artefact or an example.
    - LOW: plausible but unconfirmed; raised for the writer to check.
    """

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class QualityFinding(pydantic.BaseModel):
    """One breach of one rule, at one place in the section.

    Field order is deliberate: the LLM generates the JSON in declaration
    order, so it names the rule and the text before it explains them, and
    reasons out why it matters before it commits to a confidence.
    """

    rule_id: RuleId = pydantic.Field(
        ..., description="The ID of the rule the text breaks, e.g. GM-Qual-24"
    )
    exact: str = pydantic.Field(
        ...,
        min_length=1,
        description=(
            "The shortest span of the section that shows the issue, copied "
            "character for character from the Markdown, including any Markdown "
            "symbols in it. It must appear exactly once in the section: if the "
            "same words appear elsewhere in the section, widen the span until "
            "it is unique"
        ),
    )
    issue: str = pydantic.Field(
        ..., description="What is wrong with the quoted text, in plain English"
    )
    why_it_matters: str = pydantic.Field(
        ..., description="Why the issue matters to a reader of the guidance"
    )
    recommendation: str = pydantic.Field(
        ...,
        description="The change that would fix it, with replacement wording if possible",
    )
    confidence: ConfidenceLevel = pydantic.Field(
        ...,
        description=(
            "How sure you are the issue is real: high = clear, specific "
            "evidence; moderate = probable but could be a conversion artefact "
            "or an example; low = plausible but unconfirmed, raised for the "
            "writer to check"
        ),
    )


class CheckerOutput(pydantic.BaseModel):
    """Structured output from the quality checker for one section."""

    findings: list[QualityFinding] = pydantic.Field(
        default_factory=list,
        description="Every breach of a rule found in the section, in document order",
    )


def _default_prompt_repository() -> prompt_repo.AbstractPromptRepository:
    return prompt_repo.FileSystemPromptRepository(
        prompt_directory=os.path.join(os.path.dirname(__file__), "prompts")
    )


@dataclass
class AgentDependencies:
    """Dependencies provided to the quality checker agent."""

    part: anchors.Part
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=_default_prompt_repository
    )
