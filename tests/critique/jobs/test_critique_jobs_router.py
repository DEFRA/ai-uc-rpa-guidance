"""Tests for the critique job API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import fastapi.testclient

import app.entrypoints.fastapi
from app.critique.jobs import dependencies, models, service

DOCUMENT_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


def _make_pending_job() -> models.CritiqueJob:
    return models.CritiqueJob(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        status=models.JobStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


def _override_service_with(svc: service.CritiqueJobService) -> None:
    app.entrypoints.fastapi.app.dependency_overrides[
        dependencies.get_critique_job_service
    ] = lambda: svc


def _clear_overrides() -> None:
    app.entrypoints.fastapi.app.dependency_overrides.clear()


class TestSubmitReviewEndpoint:
    def test_returns_202_with_job_id(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.start_review = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.post(
                "/critique/jobs",
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
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.start_review = AsyncMock(
            side_effect=service.DocumentNotFoundError(DOCUMENT_ID)
        )
        _override_service_with(svc)

        try:
            response = client.post(
                "/critique/jobs",
                json={"documentId": str(DOCUMENT_ID)},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_returns_409_when_document_not_ready(self) -> None:
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.start_review = AsyncMock(
            side_effect=service.DocumentNotReadyError(DOCUMENT_ID)
        )
        _override_service_with(svc)

        try:
            response = client.post(
                "/critique/jobs",
                json={"documentId": str(DOCUMENT_ID)},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 409

    def test_returns_422_when_document_id_missing(self) -> None:
        svc = AsyncMock(spec=service.CritiqueJobService)
        _override_service_with(svc)
        try:
            response = client.post("/critique/jobs", json={})
        finally:
            _clear_overrides()
        assert response.status_code == 422

    def test_returns_422_when_document_id_invalid(self) -> None:
        svc = AsyncMock(spec=service.CritiqueJobService)
        _override_service_with(svc)
        try:
            response = client.post("/critique/jobs", json={"documentId": "not-a-uuid"})
        finally:
            _clear_overrides()
        assert response.status_code == 422


class TestGetJobEndpoint:
    def test_returns_job_when_found(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.get_job = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/critique/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["jobId"] == str(JOB_ID)
        assert data["status"] == "pending"

    def test_returns_404_when_job_not_found(self) -> None:
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.get_job = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/critique/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_result_populated_when_completed(self) -> None:
        job = models.CritiqueJob(
            id=JOB_ID,
            document_id=DOCUMENT_ID,
            status=models.JobStatus.COMPLETED,
            result={
                "status": "review_completed",
                "iterations": 1,
                "revised_document": None,
                "reports": [],
                "critique_history": [],
                "invariant_warnings": [],
                "usage": None,
            },
            created_at=NOW,
            updated_at=NOW,
        )
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.get_job = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/critique/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"]["status"] == "review_completed"
        assert data["result"]["iterations"] == 1


class TestGetLatestReviewEndpoint:
    def test_returns_latest_job_for_document(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.get_latest_for_document = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/critique/documents/{DOCUMENT_ID}/analysis")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["documentId"] == str(DOCUMENT_ID)

    def test_returns_404_when_no_jobs_exist(self) -> None:
        svc = AsyncMock(spec=service.CritiqueJobService)
        svc.get_latest_for_document = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/critique/documents/{DOCUMENT_ID}/analysis")
        finally:
            _clear_overrides()

        assert response.status_code == 404


class TestOpenApiDocumentation:
    def test_openapi_schema_includes_jobs_endpoint(self) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "/critique/jobs" in data["paths"]

    def test_openapi_schema_includes_job_by_id_endpoint(self) -> None:
        response = client.get("/openapi.json")
        data = response.json()
        assert "/critique/jobs/{job_id}" in data["paths"]

    def test_openapi_schema_includes_document_analysis_endpoint(self) -> None:
        response = client.get("/openapi.json")
        data = response.json()
        assert "/critique/documents/{document_id}/analysis" in data["paths"]
