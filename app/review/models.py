"""Data models for the guidance review module."""

import os
from dataclasses import dataclass, field
from enum import StrEnum

import pydantic

from app.infra.prompts import repository as prompt_repo


class SeverityLevel(StrEnum):
    """Issue severity levels, in increasing order of seriousness.

    - INFO: not a defect — advice on a manual check only the writer can
      perform and the agent can make no statement about.
    - LOW: no principle breached, but worth tidying.
    - MEDIUM: a clear, contained failure against a guidance design principle.
    - HIGH: a serious defect — users would struggle to complete the task.
    - CRITICAL: the guidance cannot support first-time-right task completion
      as written.
    """

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConfidenceLevel(StrEnum):
    """How sure the agent is that a finding is real, independent of severity.

    Severity says how bad the issue is if real; confidence says how likely it
    is to be real.

    - HIGH: clear, specific evidence in the document.
    - MODERATE: probable, but could be a conversion artefact or an example.
    - LOW: plausible but unconfirmed; raised mainly for the writer to check.
    """

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class Principle(StrEnum):
    """The guidance design principles the document is reviewed against.

    Must stay in sync with the principle list in the active reviewer prompt
    loaded by reviewer.py, and with the fields of PrincipleRatings — the
    prompt defines what each principle means; this enum constrains what the
    LLM may emit.
    """

    CLEAR_PURPOSE = "clear_purpose"
    STARTS_WITH_THE_READER = "starts_with_the_reader"
    TASK_FOCUSED_STRUCTURE = "task_focused_structure"
    PLAIN_ENGLISH = "plain_english"
    MULTIPLE_FORMATS = "multiple_formats"
    DECISION_LED = "decision_led"
    SCAN_FRIENDLY = "scan_friendly"
    ACCESSIBLE_BY_DEFAULT = "accessible_by_default"
    CONSISTENT = "consistent"
    USABLE_UNDER_PRESSURE = "usable_under_pressure"


class RatingLevel(StrEnum):
    """How fully a principle is applied (green / amber / red)."""

    FULLY_APPLIED = "fully_applied"
    PARTLY_APPLIED = "partly_applied"
    NOT_APPLIED = "not_applied"


class UsabilityVerdict(StrEnum):
    """Whether a user can follow the guidance under pressure, first time."""

    YES = "yes"
    PARTLY = "partly"
    NO = "no"


class TaskContext(pydantic.BaseModel):
    """The task and user context the guidance serves."""

    task: str = pydantic.Field(
        ..., description="The task this guidance is trying to help with"
    )
    user: str = pydantic.Field(..., description="Who the user of the guidance is")
    usage_context: str = pydantic.Field(
        ...,
        description=(
            "When and how the guidance is likely to be used, including any "
            "pressure, constraints, or limitations"
        ),
    )


class GoodPoint(pydantic.BaseModel):
    """A specific example of a principle being clearly applied."""

    principle: Principle = pydantic.Field(
        ..., description="The principle this example demonstrates"
    )
    quote: str = pydantic.Field(
        ...,
        min_length=1,
        description=(
            "Exact verbatim excerpt from the document showing the principle "
            "applied — copied character-for-character, never paraphrased"
        ),
    )
    comment: str = pydantic.Field(
        ..., description="Why this example works well for the user"
    )


class ReviewFinding(pydantic.BaseModel):
    """A single issue where the guidance falls short of a principle.

    Field order is deliberate: the LLM generates the JSON in declaration
    order, so the verbatim ``quote`` anchors the evidence first and
    ``why_it_matters`` is reasoned out before ``severity`` is committed to.
    """

    principle: Principle = pydantic.Field(
        ..., description="The guidance design principle this issue falls under"
    )
    section: str = pydantic.Field(
        ...,
        description="Section or location in the document where the issue was found",
    )
    quote: str = pydantic.Field(
        ...,
        min_length=1,
        description=(
            "Exact verbatim excerpt from the document evidencing the issue — "
            "copied character-for-character, never paraphrased"
        ),
    )
    issue: str = pydantic.Field(
        ...,
        description=(
            "Short one-sentence summary of the usability gap, suitable as a "
            "headline — put the detail in why_it_matters"
        ),
    )
    why_it_matters: str = pydantic.Field(
        ...,
        description=(
            "Why this issue stops the user completing the task correctly "
            "first time — reason it through before assigning the severity"
        ),
    )
    severity: SeverityLevel = pydantic.Field(
        ...,
        description=(
            "Severity implied by why_it_matters: info = manual check the writer "
            "must perform themselves; low = acceptable but worth tidying; "
            "medium = contained failure against a principle; high = users "
            "would struggle to complete the task; critical = the guidance "
            "cannot support first-time-right completion as written"
        ),
    )
    confidence: ConfidenceLevel = pydantic.Field(
        ...,
        description=(
            "How sure you are the issue is real, independent of severity: "
            "high = clear, specific evidence; moderate = probable but could be "
            "a conversion artefact or example; low = plausible but unconfirmed, "
            "raised for the writer to check"
        ),
    )
    recommendation: str = pydantic.Field(
        ...,
        description=(
            "Practical, actionable change that resolves the issue (e.g. "
            "rewrite as steps, add if/then decisions, improve headings)"
        ),
    )


class PrincipleRating(pydantic.BaseModel):
    """How fully one principle is applied.

    ``justification`` is declared before ``rating`` so the LLM reasons the
    assessment out before committing to it.
    """

    justification: str = pydantic.Field(
        ...,
        description=(
            "Evidence-based justification for the rating — reason it through "
            "before choosing the rating"
        ),
    )
    rating: RatingLevel = pydantic.Field(
        ...,
        description=(
            "How fully the principle is applied, consistent with the "
            "justification: fully_applied, partly_applied, or not_applied"
        ),
    )


class PrincipleRatings(pydantic.BaseModel):
    """One rating per guidance design principle.

    Field names must stay in sync with Principle values (tested) so every
    principle is always rated.
    """

    clear_purpose: PrincipleRating = pydantic.Field(
        ..., description="Clear purpose (task completion)"
    )
    starts_with_the_reader: PrincipleRating = pydantic.Field(
        ..., description="Starts with the reader (user context reflected)"
    )
    task_focused_structure: PrincipleRating = pydantic.Field(
        ..., description="Task-focused structure (based on actions)"
    )
    plain_english: PrincipleRating = pydantic.Field(
        ..., description="Plain English (clear, direct, unambiguous)"
    )
    multiple_formats: PrincipleRating = pydantic.Field(
        ...,
        description="Multiple formats used appropriately (steps, explanation, visuals)",
    )
    decision_led: PrincipleRating = pydantic.Field(
        ...,
        description="Decision-led (clear if/then logic and mandatory vs judgement)",
    )
    scan_friendly: PrincipleRating = pydantic.Field(
        ..., description="Scan-friendly (easy to find answers quickly)"
    )
    accessible_by_default: PrincipleRating = pydantic.Field(
        ...,
        description="Accessible by default (clear structure, logical order, inclusive)",
    )
    consistent: PrincipleRating = pydantic.Field(
        ..., description="Consistent (same terms, structure, rules)"
    )
    usable_under_pressure: PrincipleRating = pydantic.Field(
        ...,
        description="Usable under pressure (can complete task correctly first time)",
    )


class UsabilityAssessment(pydantic.BaseModel):
    """The final usability test.

    ``explanation`` is declared before ``verdict`` so the LLM reasons the
    assessment out before committing to it.
    """

    explanation: str = pydantic.Field(
        ...,
        description=(
            "Why the guidance passes, partly passes, or fails the usability "
            "test — what is missing or where it breaks down — reasoned "
            "through before choosing the verdict"
        ),
    )
    verdict: UsabilityVerdict = pydantic.Field(
        ...,
        description=(
            "Can a user follow this guidance under pressure and get it right "
            "first time, consistent with the explanation: yes, partly, or no"
        ),
    )


class ReviewOutput(pydantic.BaseModel):
    """Structured output from the guidance review.

    Judgement fields are declared after the evidence they must be consistent
    with: principle ratings after good points and findings, and the final
    ``usability`` verdict last.
    """

    document_title: str = pydantic.Field(
        ...,
        description=(
            "Exact title of the document under review; if no explicit title is "
            "visible, the first heading in the supplied content"
        ),
    )
    task_context: TaskContext = pydantic.Field(
        ..., description="The task and user context this guidance serves"
    )
    good_points: list[GoodPoint] = pydantic.Field(
        default_factory=list,
        description="Specific examples where principles are clearly applied",
    )
    findings: list[ReviewFinding] = pydantic.Field(
        default_factory=list,
        description="Issues where the guidance falls short of the principles",
    )
    principle_ratings: PrincipleRatings = pydantic.Field(
        ...,
        description=(
            "One rating per principle, decided after and consistent with the "
            "good points and findings"
        ),
    )
    usability: UsabilityAssessment = pydantic.Field(
        ...,
        description=(
            "The final usability test, decided last and consistent with "
            "everything above"
        ),
    )


@dataclass
class AgentDependencies:
    """Dependencies provided to the guidance reviewer agent."""

    document_text: str
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=lambda: prompt_repo.FileSystemPromptRepository(
            prompt_directory=os.path.join(os.path.dirname(__file__), "prompts")
        )
    )
