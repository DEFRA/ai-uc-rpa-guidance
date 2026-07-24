"""Document content port and adapter for the publishing jobs domain.

Publishing owns the DocumentContentSource Protocol (port). The wiring layer
provides GuidanceDocumentContentSource, which bridges the guidance sub-domain
without leaking its internals into publishing.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.guidance.documents import api_schemas as guidance_api_schemas
from app.guidance.documents import models as guidance_models
from app.guidance.documents import repository as guidance_repository
from app.guidance.documents import s3_repository, sectioning
from app.publishing import models as publishing_models


@dataclass
class DocumentContent:
    """Content loaded for a guidance document prior to analysis."""

    document_id: uuid.UUID
    title: str | None
    sections: list[publishing_models.DocumentSection]
    ready: bool


class DocumentContentSource(Protocol):
    """Port: provides document content to the publishing jobs domain."""

    async def get(self, document_id: uuid.UUID) -> DocumentContent | None:
        """Return content for a document, or None if it does not exist.

        Args:
            document_id: The guidance document UUID.

        Returns:
            DocumentContent with ready=False if the document exists but is not
            yet parsed, None if the document does not exist at all.
        """
        ...


class GuidanceDocumentContentSource:
    """Adapter that satisfies DocumentContentSource using the guidance sub-domain.

    Checks existence and status via the Mongo-backed guidance repository, then
    assembles each top-level section's subtree Markdown from the parsed
    artefacts in S3 (manifest + per-section files).
    """

    def __init__(
        self,
        guidance_repo: guidance_repository.GuidanceRepository,
        storage_repo: s3_repository.AbstractGuidanceStorageRepository,
    ) -> None:
        """Initialise the adapter.

        Args:
            guidance_repo: Guidance MongoDB repository.
            storage_repo: Guidance S3 storage repository.
        """
        self._guidance_repo = guidance_repo
        self._storage_repo = storage_repo

    async def get(self, document_id: uuid.UUID) -> DocumentContent | None:
        """Fetch content for a guidance document.

        Args:
            document_id: The guidance document UUID.

        Returns:
            None if the document does not exist.
            DocumentContent(ready=False) if status is not COMPLETE.
            DocumentContent(ready=True) with each top-level section's subtree
            Markdown assembled from S3 if COMPLETE.
        """
        doc = await self._guidance_repo.get_document(document_id)

        if doc is None:
            return None

        if doc.status is not guidance_models.ExtractionStatus.COMPLETE:
            return DocumentContent(
                document_id=document_id,
                title=doc.title,
                sections=[],
                ready=False,
            )

        raw_manifest = await self._storage_repo.download_manifest(document_id)
        manifest = guidance_api_schemas.DocumentManifestResponse.model_validate_json(
            raw_manifest
        )

        sections = [
            publishing_models.DocumentSection(
                number=number,
                text=await sectioning.fetch_joined_sections(
                    self._storage_repo,
                    document_id,
                    sectioning.section_and_descendant_numbers(manifest, number),
                ),
            )
            for number in sectioning.top_level_section_numbers(manifest)
        ]

        return DocumentContent(
            document_id=document_id,
            title=doc.title,
            sections=sections,
            ready=True,
        )
