"""Data models for the guidance language critique module."""

import os
from dataclasses import dataclass, field
from enum import StrEnum

import pydantic

from app import config
from app.infra.context import repository as context_repo
from app.infra.prompts import repository as prompt_repo


class Standard(StrEnum):
    """The standards a finding or conformance summary is assessed against."""

    GDS = "gds"
    DEFRA_STYLE = "defra_style"


class SeverityLevel(StrEnum):
    """Finding severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CritiqueFinding(pydantic.BaseModel):
    """A single divergence from a standard found during review."""

    standard: Standard = pydantic.Field(
        ..., description="Which standard the finding diverges from"
    )
    rule_reference: str = pydantic.Field(
        ...,
        description="The specific rule or guidance document cited, e.g. a style guide entry title",
    )
    what: str = pydantic.Field(..., description="Description of the problem")
    where: str = pydantic.Field(
        ..., description="Section or heading in the document where the issue appears"
    )
    quote: str = pydantic.Field(
        ...,
        min_length=1,
        description=(
            "Verbatim excerpt copied character-for-character from the document "
            "showing the issue (one representative instance for repeating patterns)"
        ),
    )
    why: str = pydantic.Field(
        ...,
        description="How the text diverges from the rule, quoting the rule where possible",
    )
    fix: str = pydantic.Field(
        ..., description="The specific text-level change required to resolve the issue"
    )
    severity: SeverityLevel = pydantic.Field(
        ..., description="Severity level of the finding"
    )


class ConformanceSummary(pydantic.BaseModel):
    """Per-standard summary of what was checked and found compliant."""

    standard: Standard = pydantic.Field(..., description="The standard checked")
    summary: str = pydantic.Field(
        ..., description="What was checked against this standard and found compliant"
    )


class CritiqueOutput(pydantic.BaseModel):
    """Structured output from the critic agent."""

    approved: bool = pydantic.Field(
        ...,
        description="True only when the document meets all applicable standards and no findings remain",
    )
    findings: list[CritiqueFinding] = pydantic.Field(
        default_factory=list, description="Divergences from the standards"
    )
    conformance: list[ConformanceSummary] = pydantic.Field(
        default_factory=list, description="One conformance summary per standard"
    )
    summary: str = pydantic.Field(
        ..., description="High-level summary of the review outcome"
    )


class RevisionOutput(pydantic.BaseModel):
    """Structured output from the writer agent."""

    revised_document: str = pydantic.Field(
        ..., description="The full revised document in markdown"
    )
    change_notes: str = pydantic.Field(
        ..., description="Brief notes describing the changes that were applied"
    )


def _default_prompt_repository() -> prompt_repo.AbstractPromptRepository:
    return prompt_repo.FileSystemPromptRepository(
        prompt_directory=os.path.join(os.path.dirname(__file__), "prompts")
    )


def _default_context_repository() -> context_repo.AbstractContextRepository:
    return context_repo.FileSystemContextRepository(
        context_directory=config.get_config().context_directory
    )


@dataclass
class AgentDependencies:
    """Dependencies provided to the critic and writer agents."""

    document_text: str
    previous_findings: list[CritiqueFinding] = field(default_factory=list)
    findings_to_apply: list[CritiqueFinding] = field(default_factory=list)
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=_default_prompt_repository
    )
    context_repository: context_repo.AbstractContextRepository = field(
        default_factory=_default_context_repository
    )
