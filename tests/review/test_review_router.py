"""Tests for the review API endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import fastapi.testclient

import app.entrypoints.fastapi
from app.review.jobs import dependencies, models, service

DOCUMENT_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


def _make_pending_job() -> models.ReviewJob:
    return models.ReviewJob(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        status=models.JobStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_result() -> dict[str, Any]:
    return {
        "status": "completed",
        "document_title": "Test",
        "task_context": {
            "task": "Process a claim",
            "user": "A claims processor",
            "usage_context": "Used live on calls",
        },
        "usability": {"verdict": "partly", "explanation": "Decisions unclear"},
        "principle_ratings": {
            "clear_purpose": "partly_applied",
            "starts_with_the_reader": "partly_applied",
            "task_focused_structure": "partly_applied",
            "plain_english": "partly_applied",
            "multiple_formats": "partly_applied",
            "decision_led": "partly_applied",
            "scan_friendly": "partly_applied",
            "accessible_by_default": "partly_applied",
            "consistent": "partly_applied",
            "usable_under_pressure": "partly_applied",
        },
        "good_points": [],
        "findings": [],
        "usage": None,
    }


def _override_service_with(svc: service.ReviewJobService) -> None:
    app.entrypoints.fastapi.app.dependency_overrides[
        dependencies.get_review_job_service
    ] = lambda: svc


def _clear_overrides() -> None:
    app.entrypoints.fastapi.app.dependency_overrides.clear()


class TestAnalyseEndpoint:
    def test_returns_202_with_job_id(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.start_review = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.post(
                "/review/analyse",
                json={"documentId": str(DOCUMENT_ID)},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 202
        data = response.json()
        assert data["jobId"] == str(JOB_ID)
        assert data["documentId"] == str(DOCUMENT_ID)
        assert data["status"] == "pending"
        assert data["result"] is None

    def test_returns_404_when_document_not_found(self) -> None:
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.start_review = AsyncMock(
            side_effect=service.DocumentNotFoundError(DOCUMENT_ID)
        )
        _override_service_with(svc)

        try:
            response = client.post(
                "/review/analyse",
                json={"documentId": str(DOCUMENT_ID)},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_returns_409_when_document_not_ready(self) -> None:
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.start_review = AsyncMock(
            side_effect=service.DocumentNotReadyError(DOCUMENT_ID)
        )
        _override_service_with(svc)

        try:
            response = client.post(
                "/review/analyse",
                json={"documentId": str(DOCUMENT_ID)},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 409

    def test_returns_422_when_document_id_missing(self) -> None:
        svc = AsyncMock(spec=service.ReviewJobService)
        _override_service_with(svc)
        try:
            response = client.post("/review/analyse", json={})
        finally:
            _clear_overrides()
        assert response.status_code == 422

    def test_returns_422_when_document_id_invalid(self) -> None:
        svc = AsyncMock(spec=service.ReviewJobService)
        _override_service_with(svc)
        try:
            response = client.post("/review/analyse", json={"documentId": "not-a-uuid"})
        finally:
            _clear_overrides()
        assert response.status_code == 422


class TestGetJobEndpoint:
    def test_returns_job_when_found(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.get_job = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/review/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["jobId"] == str(JOB_ID)
        assert data["status"] == "pending"

    def test_returns_404_when_job_not_found(self) -> None:
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.get_job = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/review/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_result_populated_when_completed(self) -> None:
        job = models.ReviewJob(
            id=JOB_ID,
            document_id=DOCUMENT_ID,
            status=models.JobStatus.COMPLETED,
            result=_make_result(),
            created_at=NOW,
            updated_at=NOW,
        )
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.get_job = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/review/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"]["usability"]["verdict"] == "partly"
        assert data["result"]["principle_ratings"]["decision_led"] == "partly_applied"
        assert data["result"]["task_context"]["task"] == "Process a claim"

    def test_legacy_stored_result_with_rating_objects_still_renders(self) -> None:
        """Results stored before the ratings were flattened must not 500."""
        result = _make_result()
        result["principle_ratings"] = {
            name: {"rating": "partly_applied", "justification": "j"}
            for name in result["principle_ratings"]
        }
        result["improvements"] = ["legacy field, ignored"]
        job = models.ReviewJob(
            id=JOB_ID,
            document_id=DOCUMENT_ID,
            status=models.JobStatus.COMPLETED,
            result=result,
            created_at=NOW,
            updated_at=NOW,
        )
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.get_job = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/review/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["result"]["principle_ratings"]["decision_led"] == "partly_applied"


class TestGetLatestReviewEndpoint:
    def test_returns_latest_job_for_document(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.get_latest_for_document = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/review/documents/{DOCUMENT_ID}/review")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["documentId"] == str(DOCUMENT_ID)

    def test_returns_404_when_no_jobs_exist(self) -> None:
        svc = AsyncMock(spec=service.ReviewJobService)
        svc.get_latest_for_document = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/review/documents/{DOCUMENT_ID}/review")
        finally:
            _clear_overrides()

        assert response.status_code == 404


class TestSwaggerDocumentation:
    def test_openapi_schema_includes_analyse_endpoint(self) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "/review/analyse" in data["paths"]

    def test_openapi_schema_includes_job_endpoint(self) -> None:
        response = client.get("/openapi.json")
        data = response.json()
        assert "/review/jobs/{job_id}" in data["paths"]

    def test_openapi_schema_includes_document_review_endpoint(self) -> None:
        response = client.get("/openapi.json")
        data = response.json()
        assert "/review/documents/{document_id}/review" in data["paths"]
