"""Tests for CheckExecutor status transitions."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.quality import anchors, api_schemas
from app.quality.jobs import models, submitter


def _make_job() -> models.CheckJob:
    return models.CheckJob(
        job_id=uuid.uuid4(),
        document_title="Land use guide",
        parts=anchors.top_level_parts("# T\n\n## 1 One\n\nA.\n"),
    )


def _make_executor() -> tuple[submitter.CheckExecutor, AsyncMock]:
    job_repo = AsyncMock()
    return submitter.CheckExecutor(job_repo), job_repo


def _result() -> api_schemas.QualityCheckResult:
    return api_schemas.QualityCheckResult(
        document_title="Land use guide",
        comments=[],
        usage=api_schemas.TokenUsage(input_tokens=1, output_tokens=2),
    )


class TestCheckExecutor:
    async def test_runs_then_stores_the_result(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with patch(
            "app.quality.jobs.submitter.quality_service.check_guide",
            new_callable=AsyncMock,
            return_value=_result(),
        ) as mock_check:
            await executor.execute(job)

        mock_check.assert_awaited_once()
        assert mock_check.call_args.args == (job.document_title, job.parts)
        job_repo.update_status.assert_awaited_once_with(
            job.job_id, models.JobStatus.RUNNING
        )
        job_repo.store_result.assert_awaited_once_with(
            job.job_id, _result().model_dump(mode="json")
        )
        job_repo.set_error.assert_not_called()

    async def test_records_an_error_when_the_check_fails(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        with patch(
            "app.quality.jobs.submitter.quality_service.check_guide",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Bedrock is down"),
        ):
            await executor.execute(job)

        job_repo.set_error.assert_awaited_once_with(job.job_id, "Bedrock is down")
        job_repo.store_result.assert_not_called()


class TestProgress:
    async def test_counts_each_section_as_it_finishes(self) -> None:
        executor, job_repo = _make_executor()
        job = _make_job()

        async def _check(
            _title: str, _parts: object, on_part_done: object
        ) -> api_schemas.QualityCheckResult:
            await on_part_done()  # type: ignore[operator]
            await on_part_done()  # type: ignore[operator]
            return _result()

        with patch(
            "app.quality.jobs.submitter.quality_service.check_guide",
            new_callable=AsyncMock,
            side_effect=_check,
        ):
            await executor.execute(job)

        assert job_repo.record_section_done.await_count == 2
        job_repo.record_section_done.assert_awaited_with(job.job_id)


class TestBackgroundTaskSubmitter:
    async def test_queues_the_job_as_a_background_task(self) -> None:
        background_tasks = MagicMock()
        executor, _ = _make_executor()
        job = _make_job()

        await submitter.BackgroundTaskSubmitter(background_tasks, executor).submit(job)

        background_tasks.add_task.assert_called_once_with(executor.execute, job)
