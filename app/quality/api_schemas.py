"""API schemas for quality check results: comments a client can show like Word's."""

import uuid

import pydantic
import pydantic.alias_generators


class _CamelModel(pydantic.BaseModel):
    """Serialises to camelCase for the prototype, and accepts either case."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=pydantic.alias_generators.to_camel,
        serialize_by_alias=True,
    )


class Anchor(_CamelModel):
    """Where a comment's highlight goes, as Markdown from the guide.

    The client converts all three to rendered text before it searches the
    page for them; the highlight is `exact`, found between `prefix` and
    `suffix`.
    """

    prefix: str = pydantic.Field(
        ..., description="Up to 30 characters of Markdown just before the quote"
    )
    exact: str = pydantic.Field(..., description="The quoted Markdown to highlight")
    suffix: str = pydantic.Field(
        ..., description="Up to 30 characters of Markdown just after the quote"
    )


class QualityComment(_CamelModel):
    """One finding, with what a client needs to show it as a comment on the text."""

    comment_id: uuid.UUID = pydantic.Field(
        ..., description="Unique identifier for this comment"
    )
    rule_id: str = pydantic.Field(
        ..., description="The quality rule broken, e.g. GM-Qual-24"
    )
    rule_version: int = pydantic.Field(
        ..., description="The version of the rule the check used"
    )
    section: str = pydantic.Field(
        ...,
        description='The section the text is in: "2.1", "Annex A", or its heading',
    )
    anchor: Anchor = pydantic.Field(..., description="Where to highlight")
    issue: str = pydantic.Field(..., description="What is wrong")
    why_it_matters: str = pydantic.Field(..., description="Why it matters")
    recommendation: str = pydantic.Field(..., description="How to fix it")
    confidence: str = pydantic.Field(
        ..., description="How sure the check is: high, moderate or low"
    )


class TokenUsage(_CamelModel):
    """Token usage across every section's check."""

    input_tokens: int = pydantic.Field(..., description="Input tokens consumed")
    output_tokens: int = pydantic.Field(..., description="Output tokens consumed")


class QualityCheckResult(_CamelModel):
    """The comments from checking a whole guide, in document order."""

    document_title: str = pydantic.Field(..., description="The guide's title")
    comments: list[QualityComment] = pydantic.Field(
        default_factory=list, description="Every comment, in document order"
    )
    usage: TokenUsage = pydantic.Field(..., description="Tokens used by the check")
