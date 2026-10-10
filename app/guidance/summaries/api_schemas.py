"""Pydantic request/response schemas for the guidance summaries API."""

from datetime import datetime

import pydantic
import pydantic.alias_generators

from app.guidance.summaries import models


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
    content_sha256: str | None = pydantic.Field(
        None, description="SHA-256 of the guide's content.md it was built from"
    )
    version_id: str | None = pydantic.Field(
        None, description="The guide version it was built from"
    )
    updated_at: datetime = pydantic.Field(
        ..., description="When the summary was last rebuilt"
    )


class SummaryListResponse(pydantic.BaseModel):
    """The summaries the index holds."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    items: list[SummaryResponse] = pydantic.Field(
        default_factory=list, description="The summaries"
    )


class RebuildRequest(pydantic.BaseModel):
    """What a rebuild of the index should index."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    mode: models.RebuildMode = pydantic.Field(
        models.RebuildMode.FULL,
        description=(
            "full: discard the index and index every guide. partial: index "
            "only guides whose content changed or that are new, and remove "
            "those no longer uploaded."
        ),
    )


class RebuildFailureResponse(pydantic.BaseModel):
    """A guide a rebuild could not index."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    document_id: str = pydantic.Field(..., description="The guide that failed")
    title: str = pydantic.Field(..., description="The guide's title")
    error_message: str = pydantic.Field(..., description="Why it failed")


class RebuildResponse(pydantic.BaseModel):
    """A rebuild of the index, and how far it has got."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    rebuild_id: str = pydantic.Field(..., description="The rebuild's id")
    mode: str = pydantic.Field(..., description="full or partial")
    status: str = pydantic.Field(
        ..., description="queued, running, complete, failed or cancelled"
    )
    total: int = pydantic.Field(
        ..., description="How many guides it covers, skipped ones included"
    )
    completed: int = pydantic.Field(
        ...,
        description=(
            "How many guides it has finished, indexed or failed; skipped ones "
            "count as finished from the start"
        ),
    )
    current_title: str | None = pydantic.Field(
        None, description="The guide it started last"
    )
    failures: list[RebuildFailureResponse] = pydantic.Field(
        default_factory=list, description="Guides it could not index"
    )
    purged: int = pydantic.Field(
        ...,
        description=(
            "Summaries removed: all of them for a full rebuild, those of "
            "guides no longer uploaded for a partial one"
        ),
    )
    skipped: int = pydantic.Field(
        0, description="Guides left alone because they were already up to date"
    )
    duration_seconds: float | None = pydantic.Field(
        None, description="How long it took, wall clock, once complete"
    )
    error_message: str | None = pydantic.Field(
        None, description="Why the rebuild as a whole failed"
    )
