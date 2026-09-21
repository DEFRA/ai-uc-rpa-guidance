"""Pydantic request/response schemas for the guidance summaries API."""

from datetime import datetime

import pydantic
import pydantic.alias_generators


class SummaryRebuildRequest(pydantic.BaseModel):
    """Request to rebuild the summaries for a set of documents."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    document_ids: list[pydantic.UUID4] = pydantic.Field(
        default_factory=list,
        description="The documents to summarise.",
    )


class AcronymResponse(pydantic.BaseModel):
    """One entry of a document's acronym index."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    acronym: str = pydantic.Field(..., description="The acronym as written")
    expansion: str | None = pydantic.Field(
        None, description="What it stands for, where the document says so"
    )
    sections: list[str] = pydantic.Field(
        default_factory=list, description="The sections it appears in"
    )


class SectionAcronymResponse(pydantic.BaseModel):
    """An acronym a section uses.

    It carries no section list of its own: the section it belongs to is the
    one it is returned under.
    """

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    acronym: str = pydantic.Field(..., description="The acronym as written")
    expansion: str | None = pydantic.Field(
        None, description="What it stands for, where the document says so"
    )


class SectionSummaryResponse(pydantic.BaseModel):
    """Response model for one section's index entry."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    number: str = pydantic.Field(..., description="The section's dotted number")
    heading: str = pydantic.Field(..., description="The section's heading")
    level: int = pydantic.Field(..., description="Its depth in the section graph")
    summary: str = pydantic.Field(..., description="What the section covers")
    keywords: list[str] = pydantic.Field(
        default_factory=list, description="The terms it would be searched by"
    )
    acronyms: list[SectionAcronymResponse] = pydantic.Field(
        default_factory=list, description="The acronyms this section uses"
    )
    start_path: str = pydantic.Field(
        ..., description="Path at which the section is served"
    )


class SummaryResponse(pydantic.BaseModel):
    """Response model for one document's summary."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    document_id: str = pydantic.Field(..., description="The document summarised")
    title: str = pydantic.Field(..., description="The document's title")
    about: str = pydantic.Field(..., description="What the document is about")
    used_for: str = pydantic.Field(..., description="What the document is used for")
    keywords: list[str] = pydantic.Field(
        default_factory=list, description="The terms it would be searched by"
    )
    acronyms: list[AcronymResponse] = pydantic.Field(
        default_factory=list,
        description="Reverse index of the acronyms the document uses",
    )
    path: str = pydantic.Field(
        ..., description="Storage path of the rendered summary Markdown"
    )
    start_path: str = pydantic.Field(
        ...,
        description="Path at which a reader starts reading: the first section",
    )
    sections: list[SectionSummaryResponse] = pydantic.Field(
        default_factory=list,
        description="One entry per section, in document order",
    )
    updated_at: datetime = pydantic.Field(
        ..., description="When the summary was last rebuilt"
    )


class SummaryFailureResponse(pydantic.BaseModel):
    """A document whose summary could not be rebuilt."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    document_id: str = pydantic.Field(..., description="The document that failed")
    error_message: str = pydantic.Field(..., description="Why it failed")


class SummaryListResponse(pydantic.BaseModel):
    """The summaries the index holds."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    items: list[SummaryResponse] = pydantic.Field(
        default_factory=list, description="The summaries"
    )


class SummaryRebuildResponse(SummaryListResponse):
    """What a rebuild discarded, produced, and could not produce."""

    purged: int = pydantic.Field(
        ..., description="Summaries discarded before the rebuild began"
    )
    duration_seconds: float = pydantic.Field(
        ..., description="How long the rebuild took, wall clock"
    )
    failures: list[SummaryFailureResponse] = pydantic.Field(
        default_factory=list,
        description="Documents whose summary could not be rebuilt",
    )
