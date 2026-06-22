"""Tests for AnalysisExecutor status transitions."""

import uuid
from unittest.mock import AsyncMock, patch

from app.publishing import api_schemas as publishing_schemas
from app.publishing.jobs import models, submitter


def _make_job() -> models.AnalysisJob:
    return models.AnalysisJob(
        job_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_text="# Test\n\nSome content.",
    )


def _make_executor() -> tuple[submitter.AnalysisExecutor, AsyncMock]:
    job_repo = AsyncMock()
    job_repo.update_status = AsyncMock()
    job_repo.store_result = AsyncMock()
    job_repo.set_error = AsyncMock()
    executor = submitter.AnalysisExecutor(job_repo)
    return executor, job_repo


def _mock_analyse_response() -> publishing_schemas.AnalyseResponse:
    return publishing_schemas.AnalyseResponse(
        status="completed",
        document_title="Test",
        findings=[],
        good_points=[],
        summary="All good",
        verdict="ready",
    )


class TestAnalysisExecutor:
    async def test_transitions_to_running_then_completed(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with patch(
            "app.publishing.service.checker.checker_agent.run",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_result = AsyncMock()
            mock_result.output.document_title = "Test"
            mock_result.output.findings = []
            mock_result.output.good_points = []
            mock_result.output.summary = "All good"
            mock_result.output.verdict.value = "ready"
            mock_result.usage = None
            mock_run.return_value = mock_result

            await executor.execute(job)

        job_repo.update_status.assert_called_once_with(
            job.job_id, models.JobStatus.RUNNING
        )
        job_repo.store_result.assert_called_once()
        stored_result = job_repo.store_result.call_args[0][1]
        assert stored_result["status"] == "completed"
        assert stored_result["verdict"] == "ready"

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
