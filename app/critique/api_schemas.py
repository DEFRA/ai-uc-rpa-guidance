"""API request/response schemas for the guidance language critique endpoint."""

import pydantic


class CritiqueRequest(pydantic.BaseModel):
    """Request body for the critique endpoint."""

    document_text: str = pydantic.Field(
        ...,
        min_length=1,
        description="The full markdown text of the guidance document to review",
        examples=["# Guidance Title\n\nThis is the guidance content..."],
    )
    revise: bool = pydantic.Field(
        default=False,
        description=(
            "When true, apply findings with the writer agent and re-review "
            "(critic/writer loop). When false (default), perform a single "
            "critique pass and return the reports only."
        ),
    )
    max_iterations: int | None = pydantic.Field(
        default=None,
        ge=1,
        description=(
            "Optional cap on critic/writer loop iterations (only used when "
            "revise is true). The server config value is an upper bound; a "
            "lower value may be requested."
        ),
    )


class TokenUsage(pydantic.BaseModel):
    """Accumulated token usage across all agent runs."""

    input_tokens: int = pydantic.Field(
        ..., description="Total input tokens consumed across all LLM calls"
    )
    output_tokens: int = pydantic.Field(
        ..., description="Total output tokens consumed across all LLM calls"
    )


class FindingResponse(pydantic.BaseModel):
    """A divergence from a standard, in the response."""

    rule_reference: str = pydantic.Field(
        ..., description="The specific rule or guidance document cited"
    )
    what: str = pydantic.Field(..., description="Description of the problem")
    where: str = pydantic.Field(
        ..., description="Section or heading where the issue appears"
    )
    quote: str = pydantic.Field(
        ...,
        description=(
            "Verbatim excerpt from the document showing the issue "
            "(validated against the document text)"
        ),
    )
    why: str = pydantic.Field(..., description="How the text diverges from the rule")
    fix: str = pydantic.Field(..., description="The text-level change required")
    severity: str = pydantic.Field(
        ..., description="Severity: low, medium, high, or critical"
    )


class StandardReport(pydantic.BaseModel):
    """Conformance and divergence report for one standard (AC2/AC3)."""

    standard: str = pydantic.Field(
        ..., description="The standard reported on: 'gds' or 'defra_style'"
    )
    conformance_summary: str = pydantic.Field(
        ..., description="What was checked against this standard and found compliant"
    )
    findings: list[FindingResponse] = pydantic.Field(
        default_factory=list, description="Divergences from this standard"
    )


class CritiqueIterationSummary(pydantic.BaseModel):
    """Summary of one critic pass in the loop."""

    iteration: int = pydantic.Field(..., description="1-based iteration number")
    approved: bool = pydantic.Field(
        ..., description="Whether the critic approved the document on this pass"
    )
    summary: str = pydantic.Field(..., description="The critic's summary")
    finding_count: int = pydantic.Field(
        ..., description="Number of findings raised on this pass"
    )


class CritiqueResponse(pydantic.BaseModel):
    """Response from the critique endpoint."""

    status: str = pydantic.Field(
        ...,
        description=(
            "'approved' (no findings remain), 'review_completed' (single "
            "critique pass, revise=false), or 'max_iterations_reached'"
        ),
    )
    iterations: int = pydantic.Field(
        ..., description="Number of critic passes performed"
    )
    revised_document: str | None = pydantic.Field(
        default=None,
        description=(
            "The revised markdown document; null when no revision was produced "
            "(revise=false, or the document was approved on the first pass)"
        ),
    )
    reports: list[StandardReport] = pydantic.Field(
        default_factory=list,
        description="Per-standard reports from the review of the original document",
    )
    critique_history: list[CritiqueIterationSummary] = pydantic.Field(
        default_factory=list, description="Summary of each critic pass"
    )
    invariant_warnings: list[str] = pydantic.Field(
        default_factory=list,
        description=(
            "Programmatic checks that found structural drift in the final revision "
            "(missing images/links, changed heading structure)"
        ),
    )
    usage: TokenUsage | None = pydantic.Field(
        default=None, description="Accumulated token usage across all agent runs"
    )
