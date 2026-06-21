"""Data models for the publishing QA analysis module."""

import os
from dataclasses import dataclass, field
from enum import StrEnum

import pydantic

from app.infra.prompts import repository as prompt_repo


class SeverityLevel(StrEnum):
    """Issue severity levels, in increasing order of seriousness.

    - INFO: not a defect — advice on a manual check only the writer can
      perform and the agent can make no statement about.
    - LOW: no publishing standard breached, but worth tidying.
    - MEDIUM: a clear, contained breach of a publishing standard.
    - HIGH: a serious defect — Publishing would send the document back.
    - CRITICAL: publication must not happen (e.g. real sensitive data).
    """

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(StrEnum):
    """The publishing check that produced a finding.

    Must stay in sync with the category list in prompts/checker.md — the
    prompt defines what each category means; this enum constrains what the
    LLM may emit.
    """

    HEADINGS_AND_LAYOUT = "headings_and_layout"
    IMAGES_AND_FORMATTING = "images_and_formatting"
    SENSITIVE_INFORMATION = "sensitive_information"
    LINKS = "links"
    OVERALL_PUBLISH_READINESS = "overall_publish_readiness"


class ReadinessVerdict(StrEnum):
    """Overall publish readiness verdict."""

    READY = "ready"
    NOT_READY = "not_ready"


class AnalysisFinding(pydantic.BaseModel):
    """A single quality issue found during analysis.

    Field order is deliberate: the LLM generates the JSON in declaration
    order, so ``why_it_matters`` is reasoned out before ``severity`` is
    committed to.
    """

    category: FindingCategory = pydantic.Field(
        ..., description="The publishing check this issue belongs to"
    )
    section: str = pydantic.Field(
        ..., description="Section or location in the document where the issue was found"
    )
    issue: str = pydantic.Field(..., description="Description of the quality issue")
    why_it_matters: str = pydantic.Field(
        ...,
        description=(
            "Why this issue affects publication — reason it through before "
            "assigning the severity"
        ),
    )
    severity: SeverityLevel = pydantic.Field(
        ...,
        description=(
            "Severity implied by why_it_matters: info = manual check the writer "
            "must perform themselves; low = acceptable but worth tidying; "
            "medium = contained breach of a publishing standard; high = "
            "Publishing would send the document back; critical = must not be "
            "published"
        ),
    )
    recommendation: str = pydantic.Field(
        ..., description="Recommendation for resolving the issue"
    )


class AnalysisOutput(pydantic.BaseModel):
    """Structured output from the QA agent analysis.

    ``verdict`` is declared last so the LLM decides it after producing the
    findings it must be consistent with.
    """

    document_title: str = pydantic.Field(
        ...,
        description=(
            "Exact title of the document under review; if no explicit title is "
            "visible, the first heading in the supplied content"
        ),
    )
    findings: list[AnalysisFinding] = pydantic.Field(
        default_factory=list, description="List of quality issues found"
    )
    good_points: list[str] = pydantic.Field(
        default_factory=list,
        description="Things in the document that already meet Publishing standards",
    )
    summary: str = pydantic.Field(
        ..., description="High-level summary of the analysis results"
    )
    verdict: ReadinessVerdict = pydantic.Field(
        ...,
        description=(
            "Overall publish readiness, decided after and consistent with the "
            "findings: ready, or not_ready when changes are needed"
        ),
    )


@dataclass
class AgentDependencies:
    """Dependencies provided to the publishing agent."""

    document_text: str
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=lambda: prompt_repo.FileSystemPromptRepository(
            prompt_directory=os.path.join(os.path.dirname(__file__), "prompts")
        )
    )
