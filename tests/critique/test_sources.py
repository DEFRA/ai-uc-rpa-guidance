"""Tests for the CritiqueFindingSource adapter."""

import uuid
from typing import Any
from unittest.mock import AsyncMock

from app.critique import sources
from app.critique.jobs import models as critique_models
from app.feedback import models


def _make_critique_repo(job: critique_models.CritiqueJob | None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_job = AsyncMock(return_value=job)
    return repo


def _completed_critique_job(result: dict[str, Any]) -> critique_models.CritiqueJob:
    return critique_models.CritiqueJob(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        status=critique_models.JobStatus.COMPLETED,
        result=result,
    )


class TestCritiqueFindingSource:
    async def test_returns_none_when_job_not_found(self) -> None:
        source = sources.CritiqueFindingSource(_make_critique_repo(None))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_job_not_completed(self) -> None:
        job = critique_models.CritiqueJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=critique_models.JobStatus.PENDING,
            result=None,
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_result_is_none(self) -> None:
        job = critique_models.CritiqueJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=critique_models.JobStatus.COMPLETED,
            result=None,
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_job_level_snapshot_when_finding_index_is_none(self) -> None:
        job = _completed_critique_job(
            {"status": "ok", "summary": "Looks fine", "reports": []}
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), None)

        assert result is not None
        assert result.agent == models.AgentName.CRITIC
        assert result.fields == {"status": "ok", "summary": "Looks fine"}

    async def test_returns_first_finding_by_flat_index(self) -> None:
        job = _completed_critique_job(
            {
                "status": "issues",
                "summary": "Problems",
                "reports": [
                    {"findings": [{"severity": "low", "note": "first"}]},
                    {"findings": [{"severity": "high", "note": "second"}]},
                ],
            }
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))

        result = await source.get_finding_snapshot(uuid.uuid4(), 0)

        assert result is not None
        assert result.agent == models.AgentName.CRITIC
        assert result.fields["note"] == "first"

    async def test_returns_second_finding_by_flat_index_across_reports(self) -> None:
        job = _completed_critique_job(
            {
                "status": "issues",
                "summary": "Problems",
                "reports": [
                    {"findings": [{"severity": "low", "note": "first"}]},
                    {"findings": [{"severity": "high", "note": "second"}]},
                ],
            }
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))

        result = await source.get_finding_snapshot(uuid.uuid4(), 1)

        assert result is not None
        assert result.fields["note"] == "second"

    async def test_returns_none_when_finding_index_out_of_range(self) -> None:
        job = _completed_critique_job(
            {
                "status": "ok",
                "summary": "x",
                "reports": [{"findings": [{"severity": "low"}]}],
            }
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 5)
        assert result is None

    async def test_returns_none_when_finding_index_negative(self) -> None:
        job = _completed_critique_job(
            {
                "status": "ok",
                "summary": "x",
                "reports": [{"findings": [{"severity": "low"}]}],
            }
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), -1)
        assert result is None

    async def test_returns_none_when_reports_absent_and_index_given(self) -> None:
        job = _completed_critique_job({"status": "ok", "summary": "x"})
        source = sources.CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_flattens_findings_across_multiple_reports(self) -> None:
        job = _completed_critique_job(
            {
                "status": "ok",
                "summary": "x",
                "reports": [
                    {"findings": [{"severity": "low"}, {"severity": "medium"}]},
                    {"findings": [{"severity": "high"}]},
                ],
            }
        )
        source = sources.CritiqueFindingSource(_make_critique_repo(job))

        result = await source.get_finding_snapshot(uuid.uuid4(), 2)

        assert result is not None
