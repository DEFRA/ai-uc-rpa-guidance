"""Pydantic response schemas for the guidance search API."""

import pydantic
import pydantic.alias_generators


class SearchResultResponse(pydantic.BaseModel):
    """One search result, resolved to the index entry it names."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    document_id: str = pydantic.Field(..., description="The document found")
    document_title: str = pydantic.Field(..., description="Its title")
    section_number: str | None = pydantic.Field(
        None, description="The section found, where the result is a section"
    )
    heading: str = pydantic.Field(
        ..., description="The section's heading, or the document's title"
    )
    summary: str = pydantic.Field(..., description="What the index holds about it")
    reason: str = pydantic.Field(..., description="Why it answers the query")
    start_path: str = pydantic.Field(..., description="Where it is served")
    checked: bool = pydantic.Field(
        ...,
        description="Whether the section itself was read to confirm the match",
    )


class SearchAnswerResponse(pydantic.BaseModel):
    """The overview shown above the results."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    answer: str = pydantic.Field(..., description="A few sentences for the operator")
    cited: list[SearchResultResponse] = pydantic.Field(
        default_factory=list, description="The results it was drawn from"
    )


class SearchResponse(pydantic.BaseModel):
    """What a search returns."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    query: str = pydantic.Field(..., description="The query as asked")
    answer: SearchAnswerResponse | None = pydantic.Field(
        None,
        description="The overview, where the results supported one",
    )
    results: list[SearchResultResponse] = pydantic.Field(
        default_factory=list, description="The results, most relevant first"
    )
    indexed_documents: int = pydantic.Field(
        ..., description="How many documents the search had to search"
    )
    duration_seconds: float = pydantic.Field(
        ..., description="How long the search took, wall clock"
    )
