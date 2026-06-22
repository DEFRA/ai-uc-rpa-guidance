"""Document content port and adapter for the publishing jobs domain.

Publishing owns the DocumentContentSource Protocol (port). The wiring layer
provides GuidanceDocumentContentSource, which bridges the guidance sub-domain
without leaking its internals into publishing.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.guidance.documents import models as guidance_models
from app.guidance.documents import repository as guidance_repository
from app.guidance.documents import s3_repository


@dataclass
class DocumentContent:
    """Content loaded for a guidance document prior to analysis."""

    document_id: uuid.UUID
    title: str | None
    content: str
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
    fetches the authoritative parsed markdown from S3.
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
            DocumentContent(ready=True) with markdown fetched from S3 if COMPLETE.
        """
        doc = await self._guidance_repo.get_document(document_id)

        if doc is None:
            return None

        if doc.status is not guidance_models.ExtractionStatus.COMPLETE:
            return DocumentContent(
                document_id=document_id,
                title=doc.title,
                content="",
                ready=False,
            )

        markdown = await self._storage_repo.download_content(document_id)

        return DocumentContent(
            document_id=document_id,
            title=doc.title,
            content=markdown,
            ready=True,
        )
