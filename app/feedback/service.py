"""Service orchestrating feedback creation and retrieval."""

import logging
import uuid
from datetime import UTC, datetime

from app.feedback import models, repository, sources

logger = logging.getLogger(__name__)


class JobNotFoundError(Exception):
    """Raised when the referenced job does not exist or is not completed."""


class FindingNotFoundError(Exception):
    """Raised when the finding_index is out of range for the job."""


class FeedbackAlreadyExistsError(Exception):
    """Raised when feedback already exists for this job+finding_index."""


FeedbackNotFoundError = models.FeedbackNotFoundError


class FeedbackService:
    """Orchestrates feedback lifecycle: validation, snapshot capture, persistence."""

    def __init__(
        self,
        feedback_repo: repository.AbstractFeedbackRepository,
        finding_sources: dict[models.AgentName, sources.FindingSource],
    ) -> None:
        """Initialise the service.

        Args:
            feedback_repo: Repository for persisting feedback entries.
            finding_sources: Mapping from agent name to its FindingSource adapter.
        """
        self._feedback_repo = feedback_repo
        self._finding_sources = finding_sources

    async def create_feedback(
        self,
        job_id: uuid.UUID,
        agent: models.AgentName,
        finding_index: int | None,
        verdict: models.FeedbackVerdict,
        comment: str | None,
    ) -> models.FeedbackEntry:
        """Create a new feedback entry for a finding or job.

        Args:
            job_id: The job UUID.
            agent: The agent that produced the finding.
            finding_index: 0-based finding index, or None for job-level feedback.
            verdict: The user's verdict.
            comment: Optional free-text comment.

        Returns:
            The created feedback entry.

        Raises:
            JobNotFoundError: The job does not exist or is not completed (job-level).
            FindingNotFoundError: The finding_index is out of range.
            FeedbackAlreadyExistsError: Feedback already exists for this job+finding.
        """
        source = self._finding_sources[agent]
        snapshot = await source.get_finding_snapshot(job_id, finding_index)

        if snapshot is None:
            if finding_index is None:
                raise JobNotFoundError(job_id)
            raise FindingNotFoundError(finding_index)

        existing = await self._feedback_repo.get_for_finding(job_id, finding_index)
        if existing is not None:
            raise FeedbackAlreadyExistsError(job_id, finding_index)

        now = datetime.now(tz=UTC)
        entry = models.FeedbackEntry(
            id=uuid.uuid4(),
            job_id=job_id,
            agent=agent,
            finding_index=finding_index,
            verdict=verdict,
            comment=comment,
            finding_snapshot=snapshot,
            created_at=now,
            updated_at=now,
        )
        return await self._feedback_repo.create(entry)

    async def get_feedback(self, feedback_id: uuid.UUID) -> models.FeedbackEntry | None:
        """Retrieve a feedback entry by ID.

        Args:
            feedback_id: The feedback UUID.

        Returns:
            The entry, or None if not found.
        """
        return await self._feedback_repo.get_by_id(feedback_id)

    async def get_feedback_for_job(
        self, job_id: uuid.UUID
    ) -> list[models.FeedbackEntry]:
        """Retrieve all feedback for a job.

        Args:
            job_id: The job UUID.

        Returns:
            List of feedback entries, empty if none exist.
        """
        return await self._feedback_repo.get_for_job(job_id)

    async def get_feedback_for_finding(
        self, job_id: uuid.UUID, finding_index: int
    ) -> models.FeedbackEntry | None:
        """Retrieve feedback for a specific finding.

        Args:
            job_id: The job UUID.
            finding_index: The 0-based finding index.

        Returns:
            The entry, or None if no feedback exists.
        """
        return await self._feedback_repo.get_for_finding(job_id, finding_index)

    async def update_feedback(
        self,
        feedback_id: uuid.UUID,
        verdict: models.FeedbackVerdict | None,
        comment: str | None,
    ) -> models.FeedbackEntry:
        """Update the verdict and/or comment on a feedback entry.

        Args:
            feedback_id: The feedback UUID.
            verdict: New verdict, or None to leave unchanged.
            comment: New comment, or None to leave unchanged.

        Returns:
            The updated feedback entry.

        Raises:
            FeedbackNotFoundError: No feedback entry found for the given ID.
        """
        updated = await self._feedback_repo.update(feedback_id, verdict, comment)
        if updated is None:
            raise models.FeedbackNotFoundError(feedback_id)
        return updated
