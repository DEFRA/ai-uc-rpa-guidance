"""Tests for ReviewExecutor status transitions."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.critique import api_schemas as critique_schemas
from app.critique.jobs import models, submitter


def _make_job() -> models.ReviewJob:
    return models.ReviewJob(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_text="# Test\n\nSome content.",
    )


def _make_executor() -> tuple[submitter.ReviewExecutor, AsyncMock]:
    job_repo = AsyncMock()
    job_repo.update_status = AsyncMock()
    job_repo.store_result = AsyncMock()
    job_repo.set_error = AsyncMock()
    executor = submitter.ReviewExecutor(job_repo)
    return executor, job_repo


def _mock_critique_response() -> critique_schemas.CritiqueResponse:
    return critique_schemas.CritiqueResponse(
        status="review_completed",
        iterations=1,
        reports=[],
    )


class TestReviewExecutor:
    async def test_transitions_to_running_then_completed(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with patch(
            "app.critique.service.critique_document",
            new_callable=AsyncMock,
            return_value=_mock_critique_response(),
        ):
            await executor.execute(job)

        job_repo.update_status.assert_called_once_with(
            job.job_id, models.JobStatus.RUNNING
        )
        job_repo.store_result.assert_called_once()
        stored_result = job_repo.store_result.call_args[0][1]
        assert stored_result["status"] == "review_completed"
        assert stored_result["iterations"] == 1

    async def test_passes_document_text_with_revise_disabled(self) -> None:
        executor, _ = _make_executor()
        job = _make_job()

        with patch(
            "app.critique.service.critique_document",
            new_callable=AsyncMock,
            return_value=_mock_critique_response(),
        ) as mock_critique:
            await executor.execute(job)

        mock_critique.assert_called_once_with(job.document_text, revise=False)

    async def test_sets_error_on_exception(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with patch(
            "app.critique.service.critique_document",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Bedrock unavailable"),
        ):
            await executor.execute(job)

        job_repo.set_error.assert_called_once_with(job.job_id, "Bedrock unavailable")
        job_repo.store_result.assert_not_called()


class TestBackgroundTaskSubmitter:
    async def test_enqueues_executor_execute_with_job(self) -> None:
        background_tasks = MagicMock()
        executor = AsyncMock()
        task_submitter = submitter.BackgroundTaskSubmitter(background_tasks, executor)
        job = _make_job()

        await task_submitter.submit(job)

        background_tasks.add_task.assert_called_once_with(executor.execute, job)
