"""FindingSource adapter for the critique (critic) domain."""

import uuid
from typing import Any

from app.critique.jobs import models as critique_job_models
from app.critique.jobs import repository
from app.feedback import models


class CritiqueFindingSource:
    """Adapter: extracts finding snapshots from completed critique (critic) jobs."""

    def __init__(
        self,
        job_repo: repository.AbstractCritiqueJobRepository,
    ) -> None:
        """Initialise with a critique job repository.

        Args:
            job_repo: Repository for fetching critique jobs.
        """
        self._job_repo = job_repo

    async def get_finding_snapshot(
        self, job_id: uuid.UUID, finding_index: int | None
    ) -> models.FindingSnapshot | None:
        """Return a snapshot from a critique finding.

        Findings are flattened across all reports in document order.

        Args:
            job_id: The critique job UUID.
            finding_index: 0-based flat index across all report findings, or None.

        Returns:
            FindingSnapshot, or None if the job/finding cannot be resolved.
        """
        job = await self._job_repo.get_job(job_id)

        if (
            job is None
            or job.status is not critique_job_models.JobStatus.COMPLETED
            or job.result is None
        ):
            return None

        if finding_index is None:
            return models.FindingSnapshot(
                agent=models.AgentName.CRITIC,
                fields={
                    "status": job.result.get("status"),
                    "summary": job.result.get("summary"),
                },
            )

        all_findings: list[dict[str, Any]] = [
            finding
            for report in job.result.get("reports", [])
            for finding in report.get("findings", [])
        ]

        if finding_index < 0 or finding_index >= len(all_findings):
            return None

        finding = all_findings[finding_index]
        return models.FindingSnapshot(
            agent=models.AgentName.CRITIC,
            fields=finding,
        )
