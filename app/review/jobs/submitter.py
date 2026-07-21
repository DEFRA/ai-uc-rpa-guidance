"""Job submission protocol and implementations for review jobs."""

import logging
from typing import Protocol

import fastapi

from app.review import service as review_service
from app.review.jobs import models, repository

logger = logging.getLogger(__name__)


class ReviewJobSubmitter(Protocol):
    """Protocol for submitting review jobs to a backend queue or executor."""

    async def submit(self, job: models.ReviewTask) -> None:
        """Submit a job for execution.

        Args:
            job: The review task to submit.
        """
        ...


class ReviewExecutor:
    """Runs a review job: transitions status and stores the result or error."""

    def __init__(
        self,
        job_repo: repository.AbstractReviewJobRepository,
    ) -> None:
        """Initialise with a job repository.

        Args:
            job_repo: Repository used to update job state during execution.
        """
        self._job_repo = job_repo

    async def execute(self, job: models.ReviewTask) -> None:
        """Execute a review job.

        Sets status to RUNNING, calls the reviewer agent, stores the result,
        and on any failure records the ERROR status with the exception message.

        Args:
            job: The review task to execute.
        """
        await self._job_repo.update_status(job.job_id, models.JobStatus.RUNNING)

        try:
            response = await review_service.review_document(job.document_text)
            await self._job_repo.store_result(job.job_id, response.model_dump())
            logger.info("Review job %s completed", job.job_id)
        except Exception as exc:
            logger.exception("Review job %s failed", job.job_id)
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

    async def submit(self, job: models.ReviewTask) -> None:
        """Enqueue the job as a FastAPI background task.

        Args:
            job: The review task to enqueue.
        """
        self._background_tasks.add_task(self._executor.execute, job)
