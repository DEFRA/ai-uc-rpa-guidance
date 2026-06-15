"""API request/response schemas for the publishing QA analysis endpoint."""

import pydantic


class AnalyseRequest(pydantic.BaseModel):
    """Request body for the QA analysis endpoint."""

    document_text: str = pydantic.Field(
        ...,
        min_length=1,
        description="The full text of the guidance document to analyse for quality issues",
        examples=["# Guidance Title\n\nThis is the guidance content..."],
    )


class TokenUsage(pydantic.BaseModel):
    """Token usage metadata from the LLM response."""

    input_tokens: int = pydantic.Field(
        ..., description="Number of input tokens consumed"
    )
    output_tokens: int = pydantic.Field(
        ..., description="Number of output tokens consumed"
    )


class FindingResponse(pydantic.BaseModel):
    """A quality issue finding in the response."""

    category: str = pydantic.Field(
        ..., description="The publishing check this issue belongs to"
    )
    section: str = pydantic.Field(
        ..., description="Section or location where the issue was found"
    )
    issue: str = pydantic.Field(..., description="Description of the issue")
    why_it_matters: str = pydantic.Field(
        ..., description="Why the issue affects publication"
    )
    severity: str = pydantic.Field(
        ..., description="Issue severity: info, low, medium, high, or critical"
    )
    recommendation: str = pydantic.Field(
        ..., description="Recommendation for resolving the issue"
    )


class AnalyseResponse(pydantic.BaseModel):
    """Response from the QA analysis endpoint."""

    status: str = pydantic.Field(
        ..., description="Status of the analysis: 'completed' or 'error'"
    )
    document_title: str = pydantic.Field(
        ..., description="Title of the document under review"
    )
    findings: list[FindingResponse] = pydantic.Field(
        default_factory=list, description="List of quality issues identified"
    )
    good_points: list[str] = pydantic.Field(
        default_factory=list,
        description="Things in the document that already meet Publishing standards",
    )
    summary: str = pydantic.Field(
        ...,
        description="Overall summary of the document's quality and readiness for publication",
    )
    verdict: str = pydantic.Field(
        ..., description="Overall publish readiness: 'ready' or 'not_ready'"
    )
    usage: TokenUsage | None = pydantic.Field(
        default=None, description="Token usage metadata from the LLM"
    )
