"""Job submission protocol and implementations for content review jobs."""

import logging
from typing import Protocol

import fastapi

from app.critique import service as critique_service
from app.critique.jobs import models, repository

logger = logging.getLogger(__name__)


class ReviewJobSubmitter(Protocol):
    """Protocol for submitting review jobs to a backend queue or executor."""

    async def submit(self, job: models.ReviewJob) -> None:
        """Submit a job for execution.

        Args:
            job: The review job to submit.
        """
        ...


class ReviewExecutor:
    """Runs a review job: transitions status and stores the result or error."""

    def __init__(
        self,
        job_repo: repository.AbstractCritiqueJobRepository,
    ) -> None:
        """Initialise with a job repository.

        Args:
            job_repo: Repository used to update job state during execution.
        """
        self._job_repo = job_repo

    async def execute(self, job: models.ReviewJob) -> None:
        """Execute a content review job.

        Sets status to RUNNING, calls the critic agent, stores the result,
        and on any failure records the ERROR status with the exception message.

        Args:
            job: The review job to execute.
        """
        await self._job_repo.update_status(job.job_id, models.JobStatus.RUNNING)

        try:
            response = await critique_service.critique_document(
                job.document_text,
                revise=False,
            )
            await self._job_repo.store_result(job.job_id, response.model_dump())
            logger.info("Critique job %s completed", job.job_id)
        except Exception as exc:
            logger.exception("Critique job %s failed", job.job_id)
            await self._job_repo.set_error(job.job_id, str(exc))


class BackgroundTaskSubmitter:
    """Submits review jobs as FastAPI background tasks."""

    def __init__(
        self,
        background_tasks: fastapi.BackgroundTasks,
        executor: ReviewExecutor,
    ) -> None:
        """Initialise with FastAPI background task queue and executor.

        Args:
            background_tasks: Request-scoped FastAPI BackgroundTasks.
            executor: Executor that runs the agent and updates job state.
        """
        self._background_tasks = background_tasks
        self._executor = executor

    async def submit(self, job: models.ReviewJob) -> None:
        """Enqueue the job as a FastAPI background task.

        Args:
            job: The review job to enqueue.
        """
        self._background_tasks.add_task(self._executor.execute, job)
