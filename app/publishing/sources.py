"""FindingSource adapter for the publishing (checker) domain."""

import uuid
from typing import Any

from app.feedback import models
from app.publishing.jobs import models as publishing_job_models
from app.publishing.jobs import repository


class PublishingFindingSource:
    """Adapter: extracts finding snapshots from completed publishing (checker) jobs."""

    def __init__(
        self,
        job_repo: repository.AbstractPublishingJobRepository,
    ) -> None:
        """Initialise with a publishing job repository.

        Args:
            job_repo: Repository for fetching publishing jobs.
        """
        self._job_repo = job_repo

    async def get_finding_snapshot(
        self, job_id: uuid.UUID, finding_index: int | None
    ) -> models.FindingSnapshot | None:
        """Return a snapshot from a published analysis finding.

        Args:
            job_id: The publishing job UUID.
            finding_index: 0-based index into findings, or None for job-level.

        Returns:
            FindingSnapshot, or None if the job/finding cannot be resolved.
        """
        job = await self._job_repo.get_job(job_id)

        if (
            job is None
            or job.status is not publishing_job_models.JobStatus.COMPLETED
            or job.result is None
        ):
            return None

        if finding_index is None:
            return models.FindingSnapshot(
                agent=models.AgentName.CHECKER,
                fields={
                    "summary": job.result["summary"],
                    "verdict": job.result["verdict"],
                },
            )

        findings: list[dict[str, Any]] = job.result.get("findings", [])

        if finding_index < 0 or finding_index >= len(findings):
            return None

        finding = findings[finding_index]
        return models.FindingSnapshot(
            agent=models.AgentName.CHECKER,
            fields=finding,
        )
