"""Pydantic response schemas for the prototype guides API.

manifest.json is already produced with camelCase keys by
scripts/parse_docx_for_s3.py, so these models round-trip it without renaming
anything on either side.
"""

from datetime import datetime

import pydantic
import pydantic.alias_generators


class PrototypeGuideVersion(pydantic.BaseModel):
    """A single version of a prototype guide, as recorded in manifest.json."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    version: int
    version_id: str
    created_at: datetime
    sections: int
    images: int
    content_url: str = pydantic.Field(
        ...,
        description=(
            "Key relative to prototype_guides/, i.e. "
            "<documentId>/<versionId>/content.md"
        ),
    )


class PrototypeGuide(pydantic.BaseModel):
    """A prototype guide's manifest entry: its document id and version history."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    document_id: str
    title: str
    latest_version: int
    versions: list[PrototypeGuideVersion]


class PurgeResult(pydantic.BaseModel):
    """What a purge of prototype_guides/ removed."""

    deleted: int


class UploadRequest(pydantic.BaseModel):
    """Request to open an upload session for a zip of prototype guides."""

    redirect: str


class UploadResponse(pydantic.BaseModel):
    """The CDP uploader session the browser posts the zip to."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    upload_id: str
