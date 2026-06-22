"""Tests for the publishing API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import fastapi.testclient

import app.entrypoints.fastapi
from app.publishing.jobs import dependencies, documents, models, service

DOCUMENT_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


def _make_pending_job() -> models.PublishingJob:
    return models.PublishingJob(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        status=models.JobStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


def _ready_content() -> documents.DocumentContent:
    return documents.DocumentContent(
        document_id=DOCUMENT_ID,
        title="Test Document",
        content="# Test Document\n\nThis is a test.",
        ready=True,
    )


def _override_service_with(svc: service.PublishingJobService) -> None:
    app.entrypoints.fastapi.app.dependency_overrides[
        dependencies.get_publishing_job_service
    ] = lambda: svc


def _clear_overrides() -> None:
    app.entrypoints.fastapi.app.dependency_overrides.clear()


class TestAnalyseEndpoint:
    def test_returns_202_with_job_id(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.start_analysis = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.post(
                "/publishing/analyse",
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
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.start_analysis = AsyncMock(
            side_effect=service.DocumentNotFoundError(DOCUMENT_ID)
        )
        _override_service_with(svc)

        try:
            response = client.post(
                "/publishing/analyse",
                json={"documentId": str(DOCUMENT_ID)},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_returns_409_when_document_not_ready(self) -> None:
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.start_analysis = AsyncMock(
            side_effect=service.DocumentNotReadyError(DOCUMENT_ID)
        )
        _override_service_with(svc)

        try:
            response = client.post(
                "/publishing/analyse",
                json={"documentId": str(DOCUMENT_ID)},
            )
        finally:
            _clear_overrides()

        assert response.status_code == 409

    def test_returns_422_when_document_id_missing(self) -> None:
        svc = AsyncMock(spec=service.PublishingJobService)
        _override_service_with(svc)
        try:
            response = client.post("/publishing/analyse", json={})
        finally:
            _clear_overrides()
        assert response.status_code == 422

    def test_returns_422_when_document_id_invalid(self) -> None:
        svc = AsyncMock(spec=service.PublishingJobService)
        _override_service_with(svc)
        try:
            response = client.post(
                "/publishing/analyse", json={"documentId": "not-a-uuid"}
            )
        finally:
            _clear_overrides()
        assert response.status_code == 422


class TestGetJobEndpoint:
    def test_returns_job_when_found(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.get_job = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/publishing/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["jobId"] == str(JOB_ID)
        assert data["status"] == "pending"

    def test_returns_404_when_job_not_found(self) -> None:
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.get_job = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/publishing/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 404

    def test_result_populated_when_completed(self) -> None:
        job = models.PublishingJob(
            id=JOB_ID,
            document_id=DOCUMENT_ID,
            status=models.JobStatus.COMPLETED,
            result={
                "status": "completed",
                "document_title": "Test",
                "findings": [],
                "good_points": [],
                "summary": "All good",
                "verdict": "ready",
                "usage": None,
            },
            created_at=NOW,
            updated_at=NOW,
        )
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.get_job = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/publishing/jobs/{JOB_ID}")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["result"]["verdict"] == "ready"
        assert data["result"]["summary"] == "All good"


class TestGetLatestAnalysisEndpoint:
    def test_returns_latest_job_for_document(self) -> None:
        job = _make_pending_job()
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.get_latest_for_document = AsyncMock(return_value=job)
        _override_service_with(svc)

        try:
            response = client.get(f"/publishing/documents/{DOCUMENT_ID}/analysis")
        finally:
            _clear_overrides()

        assert response.status_code == 200
        data = response.json()
        assert data["documentId"] == str(DOCUMENT_ID)

    def test_returns_404_when_no_jobs_exist(self) -> None:
        svc = AsyncMock(spec=service.PublishingJobService)
        svc.get_latest_for_document = AsyncMock(return_value=None)
        _override_service_with(svc)

        try:
            response = client.get(f"/publishing/documents/{DOCUMENT_ID}/analysis")
        finally:
            _clear_overrides()

        assert response.status_code == 404


class TestSwaggerDocumentation:
    def test_swagger_docs_available(self) -> None:
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_schema_includes_analyse_endpoint(self) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "/publishing/analyse" in data["paths"]

    def test_openapi_schema_includes_job_endpoint(self) -> None:
        response = client.get("/openapi.json")
        data = response.json()
        assert "/publishing/jobs/{job_id}" in data["paths"]

    def test_openapi_schema_includes_document_analysis_endpoint(self) -> None:
        response = client.get("/openapi.json")
        data = response.json()
        assert "/publishing/documents/{document_id}/analysis" in data["paths"]
