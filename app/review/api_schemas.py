"""API response schemas for the guidance review endpoint."""

import pydantic


class TokenUsage(pydantic.BaseModel):
    """Token usage metadata from the LLM response."""

    input_tokens: int = pydantic.Field(
        ..., description="Number of input tokens consumed"
    )
    output_tokens: int = pydantic.Field(
        ..., description="Number of output tokens consumed"
    )


class TaskContextResponse(pydantic.BaseModel):
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


class UsabilityResponse(pydantic.BaseModel):
    """The final usability test."""

    verdict: str = pydantic.Field(
        ...,
        description=(
            "Can a user follow this guidance under pressure and get it right "
            "first time: yes, partly, or no"
        ),
    )
    explanation: str = pydantic.Field(
        ...,
        description=(
            "Why the guidance passes, partly passes, or fails — what is "
            "missing or where it breaks down"
        ),
    )


class PrincipleRatingsResponse(pydantic.BaseModel):
    """One rating per guidance design principle.

    Each value is how fully the principle is applied: fully_applied,
    partly_applied, or not_applied. The evidence behind an amber or red
    rating lives in the findings for that principle.
    """

    @pydantic.field_validator("*", mode="before")
    @classmethod
    def _coerce_legacy_rating(cls, value: object) -> object:
        """Accept the pre-flattening {rating, justification} result shape.

        Earlier review runs stored each rating as an object; results are
        replayed from MongoDB verbatim, so coerce them rather than failing
        the whole job response.
        """
        if isinstance(value, dict) and "rating" in value:
            return value["rating"]
        return value

    clear_purpose: str = pydantic.Field(
        ..., description="Clear purpose (task completion)"
    )
    starts_with_the_reader: str = pydantic.Field(
        ..., description="Starts with the reader (user context reflected)"
    )
    task_focused_structure: str = pydantic.Field(
        ..., description="Task-focused structure (based on actions)"
    )
    plain_english: str = pydantic.Field(
        ..., description="Plain English (clear, direct, unambiguous)"
    )
    multiple_formats: str = pydantic.Field(
        ...,
        description="Multiple formats used appropriately (steps, explanation, visuals)",
    )
    decision_led: str = pydantic.Field(
        ...,
        description="Decision-led (clear if/then logic and mandatory vs judgement)",
    )
    scan_friendly: str = pydantic.Field(
        ..., description="Scan-friendly (easy to find answers quickly)"
    )
    accessible_by_default: str = pydantic.Field(
        ...,
        description="Accessible by default (clear structure, logical order, inclusive)",
    )
    consistent: str = pydantic.Field(
        ..., description="Consistent (same terms, structure, rules)"
    )
    usable_under_pressure: str = pydantic.Field(
        ...,
        description="Usable under pressure (can complete task correctly first time)",
    )


class GoodPointResponse(pydantic.BaseModel):
    """A specific example of a principle being clearly applied."""

    principle: str = pydantic.Field(
        ..., description="The principle this example demonstrates"
    )
    quote: str = pydantic.Field(
        ..., description="Verbatim excerpt from the content showing the principle"
    )
    comment: str = pydantic.Field(
        ..., description="Why this example works well for the user"
    )


class FindingResponse(pydantic.BaseModel):
    """An issue where the guidance falls short of a principle."""

    principle: str = pydantic.Field(
        ..., description="The guidance design principle this issue falls under"
    )
    section: str = pydantic.Field(
        ..., description="Section or location where the issue was found"
    )
    quote: str = pydantic.Field(
        ..., description="Verbatim excerpt from the content evidencing the issue"
    )
    issue: str = pydantic.Field(..., description="Description of the usability gap")
    why_it_matters: str = pydantic.Field(
        ...,
        description="Why the issue stops the user completing the task first time",
    )
    severity: str = pydantic.Field(
        ..., description="Issue severity: info, low, medium, high, or critical"
    )
    confidence: str = pydantic.Field(
        ..., description="How sure the issue is real: high, moderate, or low"
    )
    recommendation: str = pydantic.Field(
        ..., description="Practical, actionable change that resolves the issue"
    )


class ReviewResponse(pydantic.BaseModel):
    """Response from the guidance review."""

    status: str = pydantic.Field(
        ..., description="Status of the review: 'completed' or 'error'"
    )
    document_title: str = pydantic.Field(
        ..., description="Title of the document under review"
    )
    task_context: TaskContextResponse = pydantic.Field(
        ..., description="The task and user context this guidance serves"
    )
    usability: UsabilityResponse = pydantic.Field(
        ..., description="The final usability test verdict and explanation"
    )
    principle_ratings: PrincipleRatingsResponse = pydantic.Field(
        ..., description="One rating per guidance design principle"
    )
    good_points: list[GoodPointResponse] = pydantic.Field(
        default_factory=list,
        description="Specific examples where principles are clearly applied",
    )
    findings: list[FindingResponse] = pydantic.Field(
        default_factory=list,
        description="Issues where the guidance falls short of the principles",
    )
    usage: TokenUsage | None = pydantic.Field(
        default=None, description="Token usage metadata from the LLM"
    )
