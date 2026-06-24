"""Pydantic request/response schemas for the guidance API."""

from datetime import datetime

import pydantic
import pydantic.alias_generators


class DocumentUploadRequest(pydantic.BaseModel):
    """Request to initiate a document upload."""

    title: str | None = None
    description: str | None = None
    redirect: str


class DocumentUploadResponse(pydantic.BaseModel):
    """Response with upload session details."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    upload_id: str = pydantic.Field(
        ...,
        alias="uploadId",
        description="Unique identifier for the initiated upload session",
    )


class FileUploadDetail(pydantic.BaseModel):
    """Details of a single uploaded file from the CDP uploader callback."""

    model_config = pydantic.ConfigDict(extra="allow", populate_by_name=True)

    file_id: str = pydantic.Field(..., alias="fileId")
    filename: str
    file_status: str = pydantic.Field(..., alias="fileStatus")
    content_length: int = pydantic.Field(..., alias="contentLength")
    checksum_sha256: str = pydantic.Field(..., alias="checksumSha256")
    detected_content_type: str | None = pydantic.Field(
        None, alias="detectedContentType"
    )
    s3_key: str = pydantic.Field(..., alias="s3Key")
    s3_bucket: str = pydantic.Field(..., alias="s3Bucket")


class CdpUploaderStatusPayload(pydantic.BaseModel):
    """Callback payload from the CDP uploader service."""

    model_config = pydantic.ConfigDict(extra="allow", populate_by_name=True)

    upload_status: str = pydantic.Field(..., alias="uploadStatus")
    metadata: dict = pydantic.Field(default_factory=dict)
    form: dict[str, FileUploadDetail | str] = pydantic.Field(default_factory=dict)
    number_of_rejected_files: int = pydantic.Field(
        default=0, alias="numberOfRejectedFiles"
    )


class DocumentResponse(pydantic.BaseModel):
    """Response model for a guidance document."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    id: str = pydantic.Field(..., description="MongoDB document ID")
    title: str | None = pydantic.Field(None, description="Document title")
    path: str | None = pydantic.Field(
        None, description="Storage path (e.g., s3://bucket/key)"
    )
    filename: str | None = pydantic.Field(None, description="Original filename")
    status: str = pydantic.Field(..., description="Current processing status")
    content_hash: str | None = pydantic.Field(
        None, description="SHA-256 hash of uploaded file content"
    )
    content: str | None = pydantic.Field(
        None, description="Parsed markdown content (populated when status is complete)"
    )
    created_at: datetime = pydantic.Field(
        ..., description="When the document was created"
    )
    updated_at: datetime = pydantic.Field(
        ..., description="When the document was last updated"
    )
    error_message: str | None = pydantic.Field(
        None, description="Error message if processing failed"
    )


class DocumentListResponse(pydantic.BaseModel):
    """Paginated list response for documents."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    items: list[DocumentResponse] = pydantic.Field(
        default_factory=list, description="List of documents"
    )
    total: int = pydantic.Field(..., description="Total number of documents")
    page: int = pydantic.Field(..., description="Current page (1-based)")
    page_size: int = pydantic.Field(..., description="Number of items per page")


class ManifestSectionNodeResponse(pydantic.BaseModel):
    """A single node in the document section graph returned by the API."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    number: str
    heading: str
    level: int
    parent: str | None = None
    children: list[str] = pydantic.Field(default_factory=list)
    links: list[str] = pydantic.Field(default_factory=list)


class DocumentManifestResponse(pydantic.BaseModel):
    """Flat adjacency-list manifest of all sections in a parsed guidance document."""

    model_config = pydantic.ConfigDict(
        populate_by_name=True, alias_generator=pydantic.alias_generators.to_camel
    )

    document_id: str
    title: str
    sections: list[ManifestSectionNodeResponse]
