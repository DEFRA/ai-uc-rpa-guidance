"""Tests for feedback finding-source adapters."""

import uuid
from typing import Any
from unittest.mock import AsyncMock

from app.critique.jobs import models as critique_models
from app.feedback import models
from app.feedback.sources import CritiqueFindingSource, PublishingFindingSource
from app.publishing.jobs import models as publishing_models


def _make_publishing_repo(job: publishing_models.PublishingJob | None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_job = AsyncMock(return_value=job)
    return repo


def _make_critique_repo(job: critique_models.CritiqueJob | None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_job = AsyncMock(return_value=job)
    return repo


def _completed_publishing_job(
    result: dict[str, Any],
) -> publishing_models.PublishingJob:
    return publishing_models.PublishingJob(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        status=publishing_models.JobStatus.COMPLETED,
        result=result,
    )


def _completed_critique_job(result: dict[str, Any]) -> critique_models.CritiqueJob:
    return critique_models.CritiqueJob(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        status=critique_models.JobStatus.COMPLETED,
        result=result,
    )


class TestPublishingFindingSource:
    async def test_returns_none_when_job_not_found(self) -> None:
        source = PublishingFindingSource(_make_publishing_repo(None))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_job_not_completed(self) -> None:
        job = publishing_models.PublishingJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=publishing_models.JobStatus.PENDING,
            result=None,
        )
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_result_is_none(self) -> None:
        job = publishing_models.PublishingJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=publishing_models.JobStatus.COMPLETED,
            result=None,
        )
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_job_level_snapshot_when_finding_index_is_none(self) -> None:
        job = _completed_publishing_job(
            {"summary": "All good", "verdict": "pass", "findings": []}
        )
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), None)

        assert result is not None
        assert result.agent == models.AgentName.CHECKER
        assert result.severity == ""
        assert result.fields == {"summary": "All good", "verdict": "pass"}

    async def test_returns_finding_snapshot_by_index(self) -> None:
        job = _completed_publishing_job(
            {
                "summary": "Issues found",
                "verdict": "fail",
                "findings": [
                    {"severity": "low", "issue": "minor"},
                    {"severity": "high", "issue": "broken link"},
                ],
            }
        )
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 1)

        assert result is not None
        assert result.agent == models.AgentName.CHECKER
        assert result.severity == "high"
        assert result.fields["issue"] == "broken link"

    async def test_returns_none_when_finding_index_out_of_range(self) -> None:
        job = _completed_publishing_job(
            {"summary": "x", "verdict": "pass", "findings": [{"severity": "low"}]}
        )
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 5)
        assert result is None

    async def test_returns_none_when_finding_index_negative(self) -> None:
        job = _completed_publishing_job(
            {"summary": "x", "verdict": "pass", "findings": [{"severity": "low"}]}
        )
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), -1)
        assert result is None

    async def test_finding_missing_severity_defaults_to_empty_string(self) -> None:
        job = _completed_publishing_job(
            {"summary": "x", "verdict": "pass", "findings": [{"issue": "no severity"}]}
        )
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)

        assert result is not None
        assert result.severity == ""

    async def test_returns_none_when_findings_key_absent_and_index_given(self) -> None:
        job = _completed_publishing_job({"summary": "x", "verdict": "pass"})
        source = PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None


class TestCritiqueFindingSource:
    async def test_returns_none_when_job_not_found(self) -> None:
        source = CritiqueFindingSource(_make_critique_repo(None))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_job_not_completed(self) -> None:
        job = critique_models.CritiqueJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=critique_models.JobStatus.PENDING,
            result=None,
        )
        source = CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_result_is_none(self) -> None:
        job = critique_models.CritiqueJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=critique_models.JobStatus.COMPLETED,
            result=None,
        )
        source = CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_job_level_snapshot_when_finding_index_is_none(self) -> None:
        job = _completed_critique_job(
            {"status": "ok", "summary": "Looks fine", "reports": []}
        )
        source = CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), None)

        assert result is not None
        assert result.agent == models.AgentName.CRITIC
        assert result.severity == ""
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
        source = CritiqueFindingSource(_make_critique_repo(job))

        result = await source.get_finding_snapshot(uuid.uuid4(), 0)

        assert result is not None
        assert result.agent == models.AgentName.CRITIC
        assert result.severity == "low"
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
        source = CritiqueFindingSource(_make_critique_repo(job))

        result = await source.get_finding_snapshot(uuid.uuid4(), 1)

        assert result is not None
        assert result.severity == "high"
        assert result.fields["note"] == "second"

    async def test_returns_none_when_finding_index_out_of_range(self) -> None:
        job = _completed_critique_job(
            {
                "status": "ok",
                "summary": "x",
                "reports": [{"findings": [{"severity": "low"}]}],
            }
        )
        source = CritiqueFindingSource(_make_critique_repo(job))
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
        source = CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), -1)
        assert result is None

    async def test_finding_missing_severity_defaults_to_empty_string(self) -> None:
        job = _completed_critique_job(
            {
                "status": "ok",
                "summary": "x",
                "reports": [{"findings": [{"note": "no severity"}]}],
            }
        )
        source = CritiqueFindingSource(_make_critique_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)

        assert result is not None
        assert result.severity == ""

    async def test_returns_none_when_reports_absent_and_index_given(self) -> None:
        job = _completed_critique_job({"status": "ok", "summary": "x"})
        source = CritiqueFindingSource(_make_critique_repo(job))
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
        source = CritiqueFindingSource(_make_critique_repo(job))

        result = await source.get_finding_snapshot(uuid.uuid4(), 2)

        assert result is not None
        assert result.severity == "high"
