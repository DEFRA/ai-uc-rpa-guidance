"""Job submission protocol and implementations for publishing analysis jobs."""

import logging
from typing import Protocol

import fastapi

from app.publishing import service as publishing_service
from app.publishing.jobs import models, repository

logger = logging.getLogger(__name__)


class AnalysisJobSubmitter(Protocol):
    """Protocol for submitting analysis jobs to a backend queue or executor."""

    async def submit(self, job: models.AnalysisJob) -> None:
        """Submit a job for execution.

        Args:
            job: The analysis job to submit.
        """
        ...


class AnalysisExecutor:
    """Runs an analysis job: transitions status and stores the result or error."""

    def __init__(
        self,
        job_repo: repository.AbstractPublishingJobRepository,
    ) -> None:
        """Initialise with a job repository.

        Args:
            job_repo: Repository used to update job state during execution.
        """
        self._job_repo = job_repo

    async def execute(self, job: models.AnalysisJob) -> None:
        """Execute an analysis job.

        Sets status to RUNNING, calls the checker agent, stores the result,
        and on any failure records the ERROR status with the exception message.

        Args:
            job: The analysis job to execute.
        """
        await self._job_repo.update_status(job.job_id, models.JobStatus.RUNNING)

        try:
            response = await publishing_service.analyse_document(job.document_text)
            await self._job_repo.store_result(job.job_id, response.model_dump())
            logger.info("Publishing job %s completed", job.job_id)
        except Exception as exc:
            logger.exception("Publishing job %s failed", job.job_id)
            await self._job_repo.set_error(job.job_id, str(exc))


class BackgroundTaskSubmitter:
    """Submits analysis jobs as FastAPI background tasks."""

    def __init__(
        self,
        background_tasks: fastapi.BackgroundTasks,
        executor: AnalysisExecutor,
    ) -> None:
        """Initialise with FastAPI background task queue and executor.

        Args:
            background_tasks: Request-scoped FastAPI BackgroundTasks.
            executor: Executor that runs the agent and updates job state.
        """
        self._background_tasks = background_tasks
        self._executor = executor

    async def submit(self, job: models.AnalysisJob) -> None:
        """Enqueue the job as a FastAPI background task.

        Args:
            job: The analysis job to enqueue.
        """
        self._background_tasks.add_task(self._executor.execute, job)
