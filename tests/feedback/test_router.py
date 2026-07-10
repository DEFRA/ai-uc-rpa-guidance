"""Tests for the feedback API endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import fastapi.testclient

import app.entrypoints.fastapi
from app.feedback import dependencies, models, service

FEEDBACK_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_snapshot() -> models.FindingSnapshot:
    return models.FindingSnapshot(
        agent=models.AgentName.CHECKER,
        severity="high",
        fields={"issue": "Broken link", "category": "links"},
    )


def _make_entry(
    *,
    finding_index: int | None = 0,
    verdict: models.FeedbackVerdict = models.FeedbackVerdict.FIX,
    comment: str | None = None,
) -> models.FeedbackEntry:
    return models.FeedbackEntry(
        id=FEEDBACK_ID,
        job_id=JOB_ID,
        agent=models.AgentName.CHECKER,
        finding_index=finding_index,
        verdict=verdict,
        comment=comment,
        finding_snapshot=_make_snapshot(),
        created_at=NOW,
        updated_at=NOW,
    )


def _create_request(
    *,
    job_id: uuid.UUID = JOB_ID,
    agent: str = "checker",
    finding_index: int | None = 0,
    verdict: str = "fix",
    comment: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "jobId": str(job_id),
        "agent": agent,
        "verdict": verdict,
    }
    if finding_index is not None:
        body["findingIndex"] = finding_index
    if comment is not None:
        body["comment"] = comment
    return body


def _override_service_with(svc: service.FeedbackService) -> None:
    app.entrypoints.fastapi.app.dependency_overrides[
        dependencies.get_feedback_service
    ] = lambda: svc


def _clear_overrides() -> None:
    app.entrypoints.fastapi.app.dependency_overrides.clear()


client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


class TestCreateFeedback:
    def test_returns_201_with_feedback_id(self) -> None:
        entry = _make_entry()
        svc = AsyncMock(spec=service.FeedbackService)
        svc.create_feedback = AsyncMock(return_value=entry)
        _override_service_with(svc)

        try:
            response = client.post("/feedback", json=_create_request())
        finally:
            _clear_overrides()

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(FEEDBACK_ID)
        assert data["jobId"] == str(JOB_ID)
        assert data["agent"] == "checker"
        assert data["findingIndex"] == 0
        assert data["verdict"] == "fix"
        assert data["comment"] is None

    def test_response_includes_finding_snapshot(self) -> None:
        entry = _make_entry()
        svc = AsyncMock(spec=service.FeedbackService)
        svc.create_feedback = AsyncMock(return_value=entry)
        _override_service_with(svc)

        try:
            response = client.post("/feedback", json=_create_request())
        finally:
            _clear_overrides()

        data = response.json()
        assert data["findingSnapshot"] is not None
        assert data["findingSnapshot"]["severity"] == "high"
        assert data["findingSnapshot"]["fields"]["issue"] == "Broken link"

    def test_returns_201_for_job_level_feedback(self) -> None:
        entry = _make_entry(finding_index=None)
        svc = AsyncMock(spec=service.FeedbackService)
        svc.create_feedback = AsyncMock(return_value=entry)
        _override_service_with(svc)

        try:
            response = client.post(
                "/feedback", json=_create_request(finding_index=None)
            )
        finally:
            _clear_overrides()

        assert response.status_code == 201
        assert response.json()["findingIndex"] is None

    def test_returns_404_when_job_not_found(self) -> None:
        svc = AsyncMock(spec=service.FeedbackService)
        svc.create_feedback = AsyncMock(
            side_effect=service.JobNotFoundError("job not found")
        )
        _override_service_with(svc)

        try:
            response = client.post("/feedback", json=_create_request())
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_returns_404_when_finding_not_found(self) -> None:
        svc = AsyncMock(spec=service.FeedbackService)
        svc.create_feedback = AsyncMock(
            side_effect=service.FindingNotFoundError("finding not found")
        )
        _override_service_with(svc)

        try:
            response = client.post("/feedback", json=_create_request())
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_returns_409_when_feedback_already_exists(self) -> None:
        svc = AsyncMock(spec=service.FeedbackService)
        svc.create_feedback = AsyncMock(
            side_effect=service.FeedbackAlreadyExistsError("exists")
        )
        _override_service_with(svc)

        try:
            response = client.post("/feedback", json=_create_request())
        finally:
            _clear_overrides()

        assert response.status_code == 409


class TestGetFeedback:
    def test_returns_200_with_feedback(self) -> None:
        entry = _make_entry()
        svc = AsyncMock(spec=service.FeedbackService)
        svc.get_feedback = AsyncMock(return_value=entry)
        _override_service_with(svc)

        try:
            response = client.get(f"/feedback/{FEEDBACK_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        assert response.json()["id"] == str(FEEDBACK_ID)

    def test_returns_404_when_not_found(self) -> None:
        svc = AsyncMock(spec=service.FeedbackService)
        svc.get_feedback = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/feedback/{uuid.uuid4()}")
        finally:
            _clear_overrides()

        assert response.status_code == 404


class TestUpdateFeedback:
    def test_returns_200_with_updated_entry(self) -> None:
        updated = _make_entry(
            verdict=models.FeedbackVerdict.WONT_FIX, comment="Not applicable"
        )
        svc = AsyncMock(spec=service.FeedbackService)
        svc.update_feedback = AsyncMock(return_value=updated)
        _override_service_with(svc)

        try:
            response = client.put(
                f"/feedback/{FEEDBACK_ID}",
                json={"verdict": "wont_fix", "comment": "Not applicable"},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["verdict"] == "wont_fix"
        assert data["comment"] == "Not applicable"

    def test_returns_404_when_not_found(self) -> None:
        svc = AsyncMock(spec=service.FeedbackService)
        svc.update_feedback = AsyncMock(
            side_effect=service.FeedbackNotFoundError("not found")
        )
        _override_service_with(svc)

        try:
            response = client.put(
                f"/feedback/{uuid.uuid4()}",
                json={"verdict": "fix"},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 404


class TestGetFeedbackForJob:
    def test_returns_200_with_list(self) -> None:
        entries = [_make_entry(finding_index=0), _make_entry(finding_index=1)]
        svc = AsyncMock(spec=service.FeedbackService)
        svc.get_feedback_for_job = AsyncMock(return_value=entries)
        _override_service_with(svc)

        try:
            response = client.get(f"/feedback/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_returns_200_with_empty_list_when_no_feedback(self) -> None:
        svc = AsyncMock(spec=service.FeedbackService)
        svc.get_feedback_for_job = AsyncMock(return_value=[])
        _override_service_with(svc)

        try:
            response = client.get(f"/feedback/jobs/{uuid.uuid4()}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        assert response.json() == []


class TestGetFeedbackForFinding:
    def test_returns_200_with_feedback(self) -> None:
        entry = _make_entry(finding_index=2)
        svc = AsyncMock(spec=service.FeedbackService)
        svc.get_feedback_for_finding = AsyncMock(return_value=entry)
        _override_service_with(svc)

        try:
            response = client.get(f"/feedback/jobs/{JOB_ID}/findings/2")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        assert response.json()["findingIndex"] == 2

    def test_returns_404_when_no_feedback_for_finding(self) -> None:
        svc = AsyncMock(spec=service.FeedbackService)
        svc.get_feedback_for_finding = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/feedback/jobs/{uuid.uuid4()}/findings/0")
        finally:
            _clear_overrides()

        assert response.status_code == 404
