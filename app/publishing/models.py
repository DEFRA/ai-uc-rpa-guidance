"""Data models for the publishing QA analysis module."""

import os
from dataclasses import dataclass, field
from enum import StrEnum

import pydantic

from app.infra.prompts import repository as prompt_repo


class SeverityLevel(StrEnum):
    """Issue severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnalysisFinding(pydantic.BaseModel):
    """A single quality issue found during analysis."""

    section: str = pydantic.Field(
        ..., description="Section or location in the document where the issue was found"
    )
    issue: str = pydantic.Field(..., description="Description of the quality issue")
    severity: SeverityLevel = pydantic.Field(
        ..., description="Severity level of the issue"
    )
    recommendation: str = pydantic.Field(
        ..., description="Recommendation for resolving the issue"
    )


class AnalysisOutput(pydantic.BaseModel):
    """Structured output from the QA agent analysis."""

    status: str = pydantic.Field(
        ..., description="Status of the analysis (e.g., 'completed', 'error')"
    )
    findings: list[AnalysisFinding] = pydantic.Field(
        default_factory=list, description="List of quality issues found"
    )
    summary: str = pydantic.Field(
        ..., description="High-level summary of the analysis results"
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
