"""FindingSource adapter for the review (reviewer) domain."""

import uuid
from typing import Any

from app.feedback import models
from app.review.jobs import models as review_job_models
from app.review.jobs import repository


class ReviewFindingSource:
    """Adapter: extracts finding snapshots from completed review jobs."""

    def __init__(
        self,
        job_repo: repository.AbstractReviewJobRepository,
    ) -> None:
        """Initialise with a review job repository.

        Args:
            job_repo: Repository for fetching review jobs.
        """
        self._job_repo = job_repo

    async def get_finding_snapshot(
        self, job_id: uuid.UUID, finding_index: int | None
    ) -> models.FindingSnapshot | None:
        """Return a snapshot from a guidance review finding.

        Args:
            job_id: The review job UUID.
            finding_index: 0-based index into findings, or None for job-level.

        Returns:
            FindingSnapshot, or None if the job/finding cannot be resolved.
        """
        job = await self._job_repo.get_job(job_id)

        if (
            job is None
            or job.status is not review_job_models.JobStatus.COMPLETED
            or job.result is None
        ):
            return None

        if finding_index is None:
            return models.FindingSnapshot(
                agent=models.AgentName.REVIEWER,
                fields={
                    "usability_verdict": job.result["usability"]["verdict"],
                    "usability_explanation": job.result["usability"]["explanation"],
                },
            )

        findings: list[dict[str, Any]] = job.result.get("findings", [])

        if finding_index < 0 or finding_index >= len(findings):
            return None

        finding = findings[finding_index]
        return models.FindingSnapshot(
            agent=models.AgentName.REVIEWER,
            fields=finding,
        )
