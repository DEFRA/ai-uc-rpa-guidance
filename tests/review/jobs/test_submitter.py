"""Tests for ReviewExecutor status transitions."""

import uuid
from unittest.mock import AsyncMock, Mock, patch

from app.review import models as review_models
from app.review.jobs import models, submitter


def _make_job() -> models.ReviewTask:
    return models.ReviewTask(
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


def _make_output() -> review_models.ReviewOutput:
    ratings = review_models.PrincipleRatings(
        **{
            name: review_models.PrincipleRating(
                justification="j", rating=review_models.RatingLevel.PARTLY_APPLIED
            )
            for name in review_models.PrincipleRatings.model_fields
        }
    )
    return review_models.ReviewOutput(
        document_title="Test",
        task_context=review_models.TaskContext(task="t", user="u", usage_context="c"),
        principle_ratings=ratings,
        usability=review_models.UsabilityAssessment(
            explanation="e", verdict=review_models.UsabilityVerdict.PARTLY
        ),
    )


class TestReviewExecutor:
    async def test_transitions_to_running_then_completed(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        mock_result = Mock()
        mock_result.output = _make_output()
        mock_result.usage = None

        with patch(
            "app.review.service.reviewer.reviewer_agent.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            await executor.execute(job)

        job_repo.update_status.assert_called_once_with(
            job.job_id, models.JobStatus.RUNNING
        )
        job_repo.store_result.assert_called_once()
        stored_result = job_repo.store_result.call_args[0][1]
        assert stored_result["status"] == "completed"
        assert stored_result["usability"]["verdict"] == "partly"
        assert stored_result["task_context"]["task"] == "t"

    async def test_sets_error_on_exception(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with patch(
            "app.review.service.reviewer.reviewer_agent.run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Bedrock unavailable"),
        ):
            await executor.execute(job)

        job_repo.set_error.assert_called_once_with(job.job_id, "Bedrock unavailable")
        job_repo.store_result.assert_not_called()
