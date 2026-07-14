"""Tests for FeedbackService orchestration."""

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.feedback import models, service


def _make_snapshot(
    agent: models.AgentName = models.AgentName.CHECKER,
    fields: dict[str, Any] | None = None,
) -> models.FindingSnapshot:
    return models.FindingSnapshot(
        agent=agent,
        fields=fields or {"issue": "Broken link"},
    )


def _make_entry(
    job_id: uuid.UUID | None = None,
    finding_index: int | None = 0,
    verdict: models.FeedbackVerdict = models.FeedbackVerdict.FIX,
) -> models.FeedbackEntry:
    return models.FeedbackEntry(
        id=uuid.uuid4(),
        job_id=job_id or uuid.uuid4(),
        agent=models.AgentName.CHECKER,
        finding_index=finding_index,
        verdict=verdict,
        comment=None,
        finding_snapshot=_make_snapshot(),
    )


def _make_service(
    *,
    snapshot: models.FindingSnapshot | None = _make_snapshot(),
    existing_feedback: models.FeedbackEntry | None = None,
    created_entry: models.FeedbackEntry | None = None,
    updated_entry: models.FeedbackEntry | None = None,
    job_feedback: list[models.FeedbackEntry] | None = None,
) -> service.FeedbackService:
    source = AsyncMock()
    source.get_finding_snapshot = AsyncMock(return_value=snapshot)

    repo = AsyncMock()
    repo.get_for_finding = AsyncMock(return_value=existing_feedback)
    repo.create = AsyncMock(side_effect=lambda e: created_entry or e)
    repo.get_by_id = AsyncMock(return_value=None)
    repo.get_for_job = AsyncMock(return_value=job_feedback or [])
    repo.update = AsyncMock(return_value=updated_entry)

    finding_sources = {
        models.AgentName.CHECKER: source,
        models.AgentName.CRITIC: source,
    }
    return service.FeedbackService(repo, finding_sources)


class TestCreateFeedback:
    async def test_returns_entry_on_success(self) -> None:
        svc = _make_service()

        entry = await svc.create_feedback(
            job_id=uuid.uuid4(),
            agent=models.AgentName.CHECKER,
            finding_index=0,
            verdict=models.FeedbackVerdict.FIX,
            comment=None,
        )

        assert entry.verdict == models.FeedbackVerdict.FIX
        assert entry.finding_index == 0

    async def test_stores_snapshot_from_source(self) -> None:
        snapshot = _make_snapshot(fields={"issue": "Critical issue"})
        svc = _make_service(snapshot=snapshot)

        entry = await svc.create_feedback(
            job_id=uuid.uuid4(),
            agent=models.AgentName.CHECKER,
            finding_index=0,
            verdict=models.FeedbackVerdict.FIX,
            comment=None,
        )

        assert entry.finding_snapshot is not None
        assert entry.finding_snapshot.fields["issue"] == "Critical issue"

    async def test_raises_job_not_found_when_snapshot_is_none_for_job_level(
        self,
    ) -> None:
        svc = _make_service(snapshot=None)
        job_id = uuid.uuid4()

        with pytest.raises(service.JobNotFoundError):
            await svc.create_feedback(
                job_id=job_id,
                agent=models.AgentName.CHECKER,
                finding_index=None,
                verdict=models.FeedbackVerdict.FIX,
                comment=None,
            )

    async def test_raises_finding_not_found_when_snapshot_is_none_for_finding(
        self,
    ) -> None:
        svc = _make_service(snapshot=None)
        job_id = uuid.uuid4()

        with pytest.raises(service.FindingNotFoundError):
            await svc.create_feedback(
                job_id=job_id,
                agent=models.AgentName.CHECKER,
                finding_index=5,
                verdict=models.FeedbackVerdict.FIX,
                comment=None,
            )

    async def test_raises_already_exists_when_feedback_present(self) -> None:
        existing = _make_entry()
        svc = _make_service(existing_feedback=existing)
        job_id = uuid.uuid4()

        with pytest.raises(service.FeedbackAlreadyExistsError):
            await svc.create_feedback(
                job_id=job_id,
                agent=models.AgentName.CHECKER,
                finding_index=0,
                verdict=models.FeedbackVerdict.FIX,
                comment=None,
            )

    async def test_raises_already_exists_for_job_level_duplicate(self) -> None:
        existing = _make_entry(finding_index=None)
        svc = _make_service(existing_feedback=existing)
        job_id = uuid.uuid4()

        with pytest.raises(service.FeedbackAlreadyExistsError):
            await svc.create_feedback(
                job_id=job_id,
                agent=models.AgentName.CHECKER,
                finding_index=None,
                verdict=models.FeedbackVerdict.WONT_FIX,
                comment=None,
            )

    async def test_stores_comment(self) -> None:
        svc = _make_service()

        entry = await svc.create_feedback(
            job_id=uuid.uuid4(),
            agent=models.AgentName.CHECKER,
            finding_index=0,
            verdict=models.FeedbackVerdict.FALSE_POSITIVE,
            comment="This is expected behaviour.",
        )

        assert entry.comment == "This is expected behaviour."

    async def test_assigns_new_uuid_to_entry(self) -> None:
        svc = _make_service()

        entry = await svc.create_feedback(
            job_id=uuid.uuid4(),
            agent=models.AgentName.CHECKER,
            finding_index=0,
            verdict=models.FeedbackVerdict.FIX,
            comment=None,
        )

        assert isinstance(entry.id, uuid.UUID)

    async def test_raises_agent_job_mismatch_when_job_belongs_to_other_agent(
        self,
    ) -> None:
        job_id = uuid.uuid4()
        checker_source: AsyncMock = AsyncMock()
        checker_source.get_finding_snapshot = AsyncMock(return_value=None)
        critic_source: AsyncMock = AsyncMock()
        critic_source.get_finding_snapshot = AsyncMock(
            return_value=_make_snapshot(agent=models.AgentName.CRITIC)
        )
        repo: AsyncMock = AsyncMock()
        repo.get_for_finding = AsyncMock(return_value=None)

        svc = service.FeedbackService(
            repo,
            {
                models.AgentName.CHECKER: checker_source,
                models.AgentName.CRITIC: critic_source,
            },
        )

        with pytest.raises(service.AgentJobMismatchError):
            await svc.create_feedback(
                job_id=job_id,
                agent=models.AgentName.CHECKER,
                finding_index=0,
                verdict=models.FeedbackVerdict.FIX,
                comment=None,
            )

    async def test_raises_agent_job_mismatch_for_job_level_feedback(
        self,
    ) -> None:
        job_id = uuid.uuid4()
        checker_source: AsyncMock = AsyncMock()
        checker_source.get_finding_snapshot = AsyncMock(return_value=None)
        critic_source: AsyncMock = AsyncMock()
        critic_source.get_finding_snapshot = AsyncMock(
            return_value=_make_snapshot(agent=models.AgentName.CRITIC)
        )
        repo: AsyncMock = AsyncMock()
        repo.get_for_finding = AsyncMock(return_value=None)

        svc = service.FeedbackService(
            repo,
            {
                models.AgentName.CHECKER: checker_source,
                models.AgentName.CRITIC: critic_source,
            },
        )

        with pytest.raises(service.AgentJobMismatchError):
            await svc.create_feedback(
                job_id=job_id,
                agent=models.AgentName.CHECKER,
                finding_index=None,
                verdict=models.FeedbackVerdict.FIX,
                comment=None,
            )

    async def test_dispatches_to_critic_source(self) -> None:
        critic_snapshot = _make_snapshot(agent=models.AgentName.CRITIC)
        svc = _make_service(snapshot=critic_snapshot)

        entry = await svc.create_feedback(
            job_id=uuid.uuid4(),
            agent=models.AgentName.CRITIC,
            finding_index=0,
            verdict=models.FeedbackVerdict.FIX,
            comment=None,
        )

        assert entry.agent == models.AgentName.CRITIC


class TestGetFeedback:
    async def test_returns_none_when_not_found(self) -> None:
        svc = _make_service()
        result = await svc.get_feedback(uuid.uuid4())
        assert result is None


class TestGetFeedbackForJob:
    async def test_returns_empty_list_when_no_feedback(self) -> None:
        svc = _make_service(job_feedback=[])
        result = await svc.get_feedback_for_job(uuid.uuid4())
        assert result == []

    async def test_returns_all_entries_for_job(self) -> None:
        job_id = uuid.uuid4()
        entries = [
            _make_entry(job_id=job_id, finding_index=0),
            _make_entry(job_id=job_id, finding_index=1),
        ]
        svc = _make_service(job_feedback=entries)

        result = await svc.get_feedback_for_job(job_id)

        assert len(result) == 2


class TestGetFeedbackForFinding:
    async def test_returns_none_when_no_feedback(self) -> None:
        svc = _make_service(existing_feedback=None)
        result = await svc.get_feedback_for_finding(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_entry_when_present(self) -> None:
        existing = _make_entry(finding_index=2)
        svc = _make_service(existing_feedback=existing)

        result = await svc.get_feedback_for_finding(uuid.uuid4(), 2)

        assert result is not None
        assert result.finding_index == 2


class TestUpdateFeedback:
    async def test_returns_updated_entry(self) -> None:
        updated = _make_entry(verdict=models.FeedbackVerdict.WONT_FIX)
        svc = _make_service(updated_entry=updated)

        result = await svc.update_feedback(
            feedback_id=uuid.uuid4(),
            verdict=models.FeedbackVerdict.WONT_FIX,
            comment=None,
        )

        assert result.verdict == models.FeedbackVerdict.WONT_FIX

    async def test_raises_not_found_when_repo_returns_none(self) -> None:
        svc = _make_service(updated_entry=None)
        feedback_id = uuid.uuid4()

        with pytest.raises(service.FeedbackNotFoundError):
            await svc.update_feedback(
                feedback_id=feedback_id,
                verdict=models.FeedbackVerdict.FIX,
                comment=None,
            )
