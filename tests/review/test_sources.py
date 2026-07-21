"""Tests for the ReviewFindingSource adapter."""

import uuid
from typing import Any
from unittest.mock import AsyncMock

from app.feedback import models
from app.review import sources
from app.review.jobs import models as review_models


def _make_repo(job: review_models.ReviewJob | None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_job = AsyncMock(return_value=job)
    return repo


def _completed_job(result: dict[str, Any]) -> review_models.ReviewJob:
    return review_models.ReviewJob(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        status=review_models.JobStatus.COMPLETED,
        result=result,
    )


def _make_result(findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "usability": {"verdict": "partly", "explanation": "Decisions unclear"},
        "findings": findings if findings is not None else [],
    }


class TestReviewFindingSource:
    async def test_returns_none_when_job_not_found(self) -> None:
        source = sources.ReviewFindingSource(_make_repo(None))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_job_not_completed(self) -> None:
        job = review_models.ReviewJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=review_models.JobStatus.PENDING,
            result=None,
        )
        source = sources.ReviewFindingSource(_make_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_none_when_result_is_none(self) -> None:
        job = review_models.ReviewJob(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            status=review_models.JobStatus.COMPLETED,
            result=None,
        )
        source = sources.ReviewFindingSource(_make_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_job_level_snapshot_when_finding_index_is_none(self) -> None:
        job = _completed_job(_make_result())
        source = sources.ReviewFindingSource(_make_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), None)

        assert result is not None
        assert result.agent == models.AgentName.REVIEWER
        assert result.fields == {
            "usability_verdict": "partly",
            "usability_explanation": "Decisions unclear",
        }

    async def test_returns_finding_snapshot_by_index(self) -> None:
        job = _completed_job(
            _make_result(
                findings=[
                    {"severity": "low", "issue": "minor"},
                    {"severity": "high", "issue": "no if/then logic"},
                ]
            )
        )
        source = sources.ReviewFindingSource(_make_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 1)

        assert result is not None
        assert result.agent == models.AgentName.REVIEWER
        assert result.fields["issue"] == "no if/then logic"

    async def test_returns_none_when_finding_index_out_of_range(self) -> None:
        job = _completed_job(_make_result(findings=[{"severity": "low"}]))
        source = sources.ReviewFindingSource(_make_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 5)
        assert result is None

    async def test_returns_none_when_finding_index_negative(self) -> None:
        job = _completed_job(_make_result(findings=[{"severity": "low"}]))
        source = sources.ReviewFindingSource(_make_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), -1)
        assert result is None

    async def test_returns_none_when_findings_key_absent_and_index_given(self) -> None:
        job = _completed_job({"usability": {"verdict": "yes", "explanation": "Fine"}})
        source = sources.ReviewFindingSource(_make_repo(job))
        result = await source.get_finding_snapshot(uuid.uuid4(), 0)
        assert result is None
