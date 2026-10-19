"""Service orchestrating the quality check job lifecycle."""

import logging
import uuid
from datetime import UTC, datetime

from app.quality.jobs import documents, models, repository, submitter

logger = logging.getLogger(__name__)


class GuideNotFoundError(Exception):
    """Raised when the referenced prototype guide does not exist."""


class QualityJobService:
    """Orchestrates the creation and retrieval of quality check jobs."""

    def __init__(
        self,
        job_repo: repository.AbstractQualityJobRepository,
        content_source: documents.GuideContentSource,
        job_submitter: submitter.CheckJobSubmitter,
    ) -> None:
        """Initialise the service.

        Args:
            job_repo: Repository for persisting job records.
            content_source: Port for loading prototype guide content.
            job_submitter: Submitter that enqueues the job for execution.
        """
        self._job_repo = job_repo
        self._content_source = content_source
        self._job_submitter = job_submitter

    async def start_check(self, document_id: uuid.UUID) -> models.QualityJob:
        """Create and submit a quality check of a guide's latest version.

        Args:
            document_id: The prototype guide to check.

        Returns:
            The created job with status PENDING.

        Raises:
            GuideNotFoundError: If the guide does not exist.
        """
        content = await self._content_source.get(document_id)

        if content is None:
            raise GuideNotFoundError(document_id)

        now = datetime.now(tz=UTC)
        job = models.QualityJob(
            id=uuid.uuid4(),
            document_id=document_id,
            version_id=content.version_id,
            status=models.JobStatus.PENDING,
            sections_total=len(content.parts),
            created_at=now,
            updated_at=now,
        )
        await self._job_repo.create_job(job)

        await self._job_submitter.submit(
            models.CheckJob(
                job_id=job.id,
                document_title=content.title or "Untitled guide",
                parts=content.parts,
            )
        )

        logger.info("Started quality job %s for guide %s", job.id, document_id)

        return job

    async def get_job(self, job_id: uuid.UUID) -> models.QualityJob | None:
        """Retrieve a job by its ID.

        Args:
            job_id: The job UUID.

        Returns:
            The job, or None if not found.
        """
        return await self._job_repo.get_job(job_id)

    async def get_latest_for_document(
        self, document_id: uuid.UUID
    ) -> models.QualityJob | None:
        """Retrieve the most recent quality check job for a guide.

        Args:
            document_id: The guide's document id.

        Returns:
            The latest job, or None if the guide has never been checked.
        """
        return await self._job_repo.get_latest_for_document(document_id)
