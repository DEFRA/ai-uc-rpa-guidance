"""FindingSource port and adapters for the feedback domain."""

import uuid
from typing import Any, Protocol

from app.critique.jobs import models as critique_job_models
from app.critique.jobs import repository as critique_repository
from app.feedback import models
from app.publishing.jobs import models as publishing_job_models
from app.publishing.jobs import repository as publishing_repository


class FindingSource(Protocol):
    """Port: validates a job+finding reference and returns a snapshot of the finding."""

    async def get_finding_snapshot(
        self, job_id: uuid.UUID, finding_index: int | None
    ) -> models.FindingSnapshot | None:
        """Return a snapshot of the finding, or None if the reference is invalid.

        Args:
            job_id: The job UUID.
            finding_index: 0-based index into the findings list, or None for job-level.

        Returns:
            FindingSnapshot if the job is completed and the index is in range, else None.
        """
        ...


class PublishingFindingSource:
    """Adapter: extracts finding snapshots from completed publishing (checker) jobs."""

    def __init__(
        self,
        job_repo: publishing_repository.AbstractPublishingJobRepository,
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
                severity="",
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
            severity=finding.get("severity", ""),
            fields=finding,
        )


class CritiqueFindingSource:
    """Adapter: extracts finding snapshots from completed critique (critic) jobs."""

    def __init__(
        self,
        job_repo: critique_repository.AbstractCritiqueJobRepository,
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
                severity="",
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
            severity=finding.get("severity", ""),
            fields=finding,
        )
