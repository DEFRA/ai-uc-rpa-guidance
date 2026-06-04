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

    section: str = pydantic.Field(
        ..., description="Section or location where the issue was found"
    )
    issue: str = pydantic.Field(..., description="Description of the issue")
    severity: str = pydantic.Field(
        ..., description="Issue severity: low, medium, high, or critical"
    )
    recommendation: str = pydantic.Field(
        ..., description="Recommendation for resolving the issue"
    )


class AnalyseResponse(pydantic.BaseModel):
    """Response from the QA analysis endpoint."""

    status: str = pydantic.Field(
        ..., description="Status of the analysis: 'completed' or 'error'"
    )
    findings: list[FindingResponse] = pydantic.Field(
        default_factory=list, description="List of quality issues identified"
    )
    summary: str = pydantic.Field(
        ...,
        description="Overall summary of the document's quality and readiness for publication",
    )
    usage: TokenUsage | None = pydantic.Field(
        default=None, description="Token usage metadata from the LLM"
    )
