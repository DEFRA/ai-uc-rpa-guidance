"""Job submission protocol and implementations for quality check jobs."""

import logging
from typing import Protocol

import fastapi

from app.quality import service as quality_service
from app.quality.jobs import models, repository

logger = logging.getLogger(__name__)


class CheckJobSubmitter(Protocol):
    """Protocol for submitting check jobs to a backend queue or executor."""

    async def submit(self, job: models.CheckJob) -> None:
        """Submit a job for execution.

        Args:
            job: The check job to submit.
        """
        ...


class CheckExecutor:
    """Runs a check job: transitions status and stores the result or error."""

    def __init__(self, job_repo: repository.AbstractQualityJobRepository) -> None:
        """Initialise with a job repository.

        Args:
            job_repo: Repository used to update job state during execution.
        """
        self._job_repo = job_repo

    async def execute(self, job: models.CheckJob) -> None:
        """Execute a check job.

        Sets status to RUNNING, checks the guide, stores the result, and on
        any failure records the ERROR status with the exception message.

        Args:
            job: The check job to execute.
        """
        await self._job_repo.update_status(job.job_id, models.JobStatus.RUNNING)

        try:
            result = await quality_service.check_guide(
                job.document_title,
                job.parts,
                on_part_done=lambda: self._job_repo.record_section_done(job.job_id),
            )
            await self._job_repo.store_result(
                job.job_id, result.model_dump(mode="json")
            )
            logger.info("Quality job %s completed", job.job_id)
        except Exception as exc:
            logger.exception("Quality job %s failed", job.job_id)
            await self._job_repo.set_error(job.job_id, str(exc))


class BackgroundTaskSubmitter:
    """Submits check jobs as FastAPI background tasks."""

    def __init__(
        self, background_tasks: fastapi.BackgroundTasks, executor: CheckExecutor
    ) -> None:
        """Initialise with FastAPI background task queue and executor.

        Args:
            background_tasks: Request-scoped FastAPI BackgroundTasks.
            executor: Executor that runs the checker and updates job state.
        """
        self._background_tasks = background_tasks
        self._executor = executor

    async def submit(self, job: models.CheckJob) -> None:
        """Enqueue the job as a FastAPI background task.

        Args:
            job: The check job to enqueue.
        """
        self._background_tasks.add_task(self._executor.execute, job)
