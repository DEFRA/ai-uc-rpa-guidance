"""Tests for AnalysisExecutor status transitions."""

import types
import uuid
from unittest.mock import AsyncMock, patch

from app.publishing import models as publishing_models
from app.publishing.jobs import models, submitter


def _make_job() -> models.AnalysisJob:
    return models.AnalysisJob(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="Test",
        sections=[
            publishing_models.DocumentSection(
                number="1", text="## 1 One\n\nSome content.\n"
            )
        ],
    )


def _make_executor() -> tuple[submitter.AnalysisExecutor, AsyncMock]:
    job_repo = AsyncMock()
    job_repo.update_status = AsyncMock()
    job_repo.store_result = AsyncMock()
    job_repo.set_error = AsyncMock()
    executor = submitter.AnalysisExecutor(job_repo)
    return executor, job_repo


def _checker_result() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        output=publishing_models.AnalysisOutput(
            findings=[],
            good_points=[],
            summary="All good",
            verdict=publishing_models.ReadinessVerdict.READY,
        ),
        usage=None,
    )


def _aggregator_result() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        output=publishing_models.AggregatedSummary(summary="Document reads well."),
        usage=None,
    )


class TestAnalysisExecutor:
    async def test_transitions_to_running_then_completed(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with (
            patch(
                "app.publishing.service.checker.checker_agent.run",
                new_callable=AsyncMock,
                return_value=_checker_result(),
            ),
            patch(
                "app.publishing.service.aggregator.aggregator_agent.run",
                new_callable=AsyncMock,
                return_value=_aggregator_result(),
            ),
        ):
            await executor.execute(job)

        job_repo.update_status.assert_called_once_with(
            job.job_id, models.JobStatus.RUNNING
        )
        job_repo.store_result.assert_called_once()
        stored_result = job_repo.store_result.call_args[0][1]
        assert stored_result["status"] == "completed"
        assert stored_result["document_title"] == "Test"
        assert stored_result["verdict"] == "ready"
        assert stored_result["summary"] == "Document reads well."

    async def test_sets_error_on_exception(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with patch(
            "app.publishing.service.checker.checker_agent.run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Bedrock unavailable"),
        ):
            await executor.execute(job)

        job_repo.set_error.assert_called_once_with(job.job_id, "Bedrock unavailable")
        job_repo.store_result.assert_not_called()
