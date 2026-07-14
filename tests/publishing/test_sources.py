"""Tests for the PublishingFindingSource adapter."""

import uuid
from typing import Any
from unittest.mock import AsyncMock

from app.feedback import models
from app.publishing import sources
from app.publishing.jobs import models as publishing_models


def _make_publishing_repo(job: publishing_models.PublishingJob | None) -> AsyncMock:
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


class TestPublishingFindingSource:
    async def test_returns_none_when_job_not_found(self) -> None:
        source = sources.PublishingFindingSource(_make_publishing_repo(None))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_job_not_completed(self) -> None:
        job = publishing_models.PublishingJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=publishing_models.JobStatus.PENDING,
            result=None,
        )
        source = sources.PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_result_is_none(self) -> None:
        job = publishing_models.PublishingJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=publishing_models.JobStatus.COMPLETED,
            result=None,
        )
        source = sources.PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_job_level_snapshot_when_finding_index_is_none(self) -> None:
        job = _completed_publishing_job(
            {"summary": "All good", "verdict": "pass", "findings": []}
        )
        source = sources.PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), None)

        assert result is not None
        assert result.agent == models.AgentName.CHECKER
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
        source = sources.PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 1)

        assert result is not None
        assert result.agent == models.AgentName.CHECKER
        assert result.fields["issue"] == "broken link"

    async def test_returns_none_when_finding_index_out_of_range(self) -> None:
        job = _completed_publishing_job(
            {"summary": "x", "verdict": "pass", "findings": [{"severity": "low"}]}
        )
        source = sources.PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 5)
        assert result is None

    async def test_returns_none_when_finding_index_negative(self) -> None:
        job = _completed_publishing_job(
            {"summary": "x", "verdict": "pass", "findings": [{"severity": "low"}]}
        )
        source = sources.PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), -1)
        assert result is None

    async def test_returns_none_when_findings_key_absent_and_index_given(self) -> None:
        job = _completed_publishing_job({"summary": "x", "verdict": "pass"})
        source = sources.PublishingFindingSource(_make_publishing_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None
