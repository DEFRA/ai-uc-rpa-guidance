"""Service orchestrating content review (critique) job lifecycle."""

import logging
import uuid
from datetime import UTC, datetime

from app.critique.jobs import documents, models, repository, submitter

logger = logging.getLogger(__name__)


class DocumentNotFoundError(Exception):
    """Raised when the referenced guidance document does not exist."""


class DocumentNotReadyError(Exception):
    """Raised when the referenced document has not been fully parsed."""


class CritiqueJobService:
    """Orchestrates the creation and retrieval of content review jobs."""

    def __init__(
        self,
        job_repo: repository.AbstractCritiqueJobRepository,
        content_source: documents.DocumentContentSource,
        job_submitter: submitter.ReviewJobSubmitter,
    ) -> None:
        """Initialise the service.

        Args:
            job_repo: Repository for persisting job records.
            content_source: Port for loading document content.
            job_submitter: Submitter that enqueues the job for execution.
        """
        self._job_repo = job_repo
        self._content_source = content_source
        self._job_submitter = job_submitter

    async def start_review(self, document_id: uuid.UUID) -> models.CritiqueJob:
        """Create and submit a content review job for a guidance document.

        Args:
            document_id: The guidance document to review.

        Returns:
            The created job with status PENDING.

        Raises:
            DocumentNotFoundError: If the document does not exist.
            DocumentNotReadyError: If the document has not been fully parsed.
        """
        content = await self._content_source.get(document_id)

        if content is None:
            raise DocumentNotFoundError(document_id)

        if not content.ready:
            raise DocumentNotReadyError(document_id)

        now = datetime.now(tz=UTC)
        job = models.CritiqueJob(
            id=uuid.uuid4(),
            document_id=document_id,
            status=models.JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        await self._job_repo.create_job(job)

        review_job = models.ReviewJob(
            job_id=job.id,
            document_id=document_id,
            document_text=content.content,
        )
        await self._job_submitter.submit(review_job)

        logger.info("Started critique job %s for document %s", job.id, document_id)

        return job

    async def get_job(self, job_id: uuid.UUID) -> models.CritiqueJob | None:
        """Retrieve a job by its ID.

        Args:
            job_id: The job UUID.

        Returns:
            The job, or None if not found.
        """
        return await self._job_repo.get_job(job_id)

    async def get_latest_for_document(
        self, document_id: uuid.UUID
    ) -> models.CritiqueJob | None:
        """Retrieve the most recent review job for a document.

        Args:
            document_id: The guidance document UUID.

        Returns:
            The latest job, or None if no jobs exist for the document.
        """
        return await self._job_repo.get_latest_for_document(document_id)
