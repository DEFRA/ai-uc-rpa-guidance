"""Tests for the guidance index rebuild endpoints."""

import uuid
from collections.abc import Generator
from unittest.mock import AsyncMock

import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.guidance.summaries import api_schemas, dependencies, models, service, worker

_REBUILD_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")

_QUEUED = api_schemas.RebuildResponse(
    rebuild_id=str(_REBUILD_ID),
    mode="full",
    status="queued",
    total=0,
    completed=0,
    purged=0,
)


@pytest.fixture
def summary_service() -> AsyncMock:
    return AsyncMock(spec=service.SummaryService)


@pytest.fixture
def rebuild_queue() -> AsyncMock:
    return AsyncMock(spec=worker.RebuildQueue)


@pytest.fixture
def client(
    summary_service: AsyncMock, rebuild_queue: AsyncMock
) -> Generator[fastapi.testclient.TestClient]:
    test_app = app.entrypoints.fastapi.app
    test_app.dependency_overrides[dependencies.get_summary_service] = lambda: (
        summary_service
    )
    test_app.dependency_overrides[worker.get_rebuild_queue] = lambda: rebuild_queue
    yield fastapi.testclient.TestClient(test_app)
    test_app.dependency_overrides.clear()


class TestRequestRebuild:
    def test_queues_a_rebuild_and_accepts_at_once(
        self,
        client: fastapi.testclient.TestClient,
        summary_service: AsyncMock,
        rebuild_queue: AsyncMock,
    ) -> None:
        summary_service.request_rebuild.return_value = _QUEUED

        response = client.post("/guidance/summaries/rebuild")

        assert response.status_code == 202
        assert response.json()["rebuildId"] == str(_REBUILD_ID)
        assert response.json()["status"] == "queued"
        rebuild_queue.submit.assert_awaited_once_with(_REBUILD_ID)
        summary_service.request_rebuild.assert_awaited_once_with(
            models.RebuildMode.FULL
        )

    def test_queues_a_partial_rebuild_when_asked(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.request_rebuild.return_value = _QUEUED

        response = client.post("/guidance/summaries/rebuild", json={"mode": "partial"})

        assert response.status_code == 202
        summary_service.request_rebuild.assert_awaited_once_with(
            models.RebuildMode.PARTIAL
        )

    def test_rejects_an_unknown_mode(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        response = client.post("/guidance/summaries/rebuild", json={"mode": "most"})

        assert response.status_code == 422
        summary_service.request_rebuild.assert_not_awaited()

    def test_refuses_while_another_rebuild_is_unfinished(
        self,
        client: fastapi.testclient.TestClient,
        summary_service: AsyncMock,
        rebuild_queue: AsyncMock,
    ) -> None:
        summary_service.request_rebuild.side_effect = service.RebuildInProgressError(
            "Already running"
        )

        response = client.post("/guidance/summaries/rebuild")

        assert response.status_code == 409
        assert response.json()["detail"] == "Already running"
        rebuild_queue.submit.assert_not_awaited()


class TestGetRebuild:
    def test_returns_the_rebuilds_progress(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.get_rebuild.return_value = _QUEUED

        response = client.get(f"/guidance/summaries/rebuilds/{_REBUILD_ID}")

        assert response.status_code == 200
        assert response.json() == {
            "rebuildId": str(_REBUILD_ID),
            "mode": "full",
            "status": "queued",
            "total": 0,
            "completed": 0,
            "currentTitle": None,
            "failures": [],
            "purged": 0,
            "skipped": 0,
            "durationSeconds": None,
            "errorMessage": None,
        }

    def test_names_a_failures_fields_in_camel_case(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.get_rebuild.return_value = _QUEUED.model_copy(
            update={
                "failures": [
                    api_schemas.RebuildFailureResponse(
                        document_id="abc", title="A guide", error_message="No"
                    )
                ]
            }
        )

        response = client.get(f"/guidance/summaries/rebuilds/{_REBUILD_ID}")

        assert response.json()["failures"] == [
            {"documentId": "abc", "title": "A guide", "errorMessage": "No"}
        ]
        summary_service.get_rebuild.assert_awaited_once_with(_REBUILD_ID)

    def test_returns_404_for_an_unknown_rebuild(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.get_rebuild.return_value = None

        response = client.get(f"/guidance/summaries/rebuilds/{_REBUILD_ID}")

        assert response.status_code == 404


class TestCurrentRebuild:
    def test_returns_the_unfinished_rebuild(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.get_current_rebuild.return_value = _QUEUED

        response = client.get("/guidance/summaries/rebuilds/current")

        assert response.status_code == 200
        assert response.json()["rebuildId"] == str(_REBUILD_ID)

    def test_returns_404_when_nothing_is_unfinished(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.get_current_rebuild.return_value = None

        response = client.get("/guidance/summaries/rebuilds/current")

        assert response.status_code == 404


class TestCancelRebuild:
    def test_cancels_through_the_queue_and_returns_the_rebuild(
        self,
        client: fastapi.testclient.TestClient,
        summary_service: AsyncMock,
        rebuild_queue: AsyncMock,
    ) -> None:
        summary_service.cancel_rebuild.return_value = _QUEUED.model_copy(
            update={"status": "cancelled", "duration_seconds": 3.2}
        )

        response = client.post(f"/guidance/summaries/rebuilds/{_REBUILD_ID}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert response.json()["durationSeconds"] == 3.2
        summary_service.cancel_rebuild.assert_awaited_once_with(
            _REBUILD_ID, rebuild_queue.cancel
        )

    def test_returns_404_for_an_unknown_rebuild(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.cancel_rebuild.side_effect = service.RebuildNotFoundError(
            "No rebuild"
        )

        response = client.post(f"/guidance/summaries/rebuilds/{_REBUILD_ID}/cancel")

        assert response.status_code == 404

    def test_returns_409_for_a_finished_rebuild(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.cancel_rebuild.side_effect = service.RebuildNotActiveError(
            "Already complete"
        )

        response = client.post(f"/guidance/summaries/rebuilds/{_REBUILD_ID}/cancel")

        assert response.status_code == 409
        assert response.json()["detail"] == "Already complete"


class TestListSummaries:
    def test_names_the_content_hash_and_version_in_camel_case(
        self, client: fastapi.testclient.TestClient, summary_service: AsyncMock
    ) -> None:
        summary_service.list_summaries.return_value = api_schemas.SummaryListResponse(
            items=[
                api_schemas.SummaryResponse(
                    document_id="abc",
                    title="A guide",
                    about="About.",
                    used_for="Used.",
                    path="s3://bucket/summary.md",
                    start_path="/prototype/guides/abc/content",
                    content_sha256="ab" * 32,
                    version_id="v-2",
                    updated_at="2026-10-10T00:00:00Z",
                )
            ]
        )

        response = client.get("/guidance/summaries/")

        item = response.json()["items"][0]
        assert item["contentSha256"] == "ab" * 32
        assert item["versionId"] == "v-2"
