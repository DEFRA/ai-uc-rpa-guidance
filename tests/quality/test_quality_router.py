"""Tests for the quality-checks API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import fastapi.testclient

import app.entrypoints.fastapi
from app.quality.jobs import dependencies, models, service

DOCUMENT_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
COMMENT_ID = uuid.uuid4()
VERSION_ID = "ad1680a5-b12f-4b1f-9b7d-6ec1539c6fdf"
NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)

RESULT = {
    "document_title": "Land use guide",
    "comments": [
        {
            "comment_id": str(COMMENT_ID),
            "rule_id": "GM-Qual-24",
            "rule_version": 1,
            "section": "2.1",
            "anchor": {
                "prefix": "Complete a review of CRM to ",
                "exact": "confirm is any correspondence",
                "suffix": " has been received.",
            },
            "issue": "'is' should be 'if'.",
            "why_it_matters": "The error makes the instruction harder to read.",
            "recommendation": "Change 'is' to 'if'.",
            "confidence": "high",
        }
    ],
    "usage": {"input_tokens": 100, "output_tokens": 10},
}


def _job(
    status: models.JobStatus = models.JobStatus.PENDING,
    result: dict[str, object] | None = None,
) -> models.QualityJob:
    return models.QualityJob(
        id=JOB_ID,
        document_id=DOCUMENT_ID,
        version_id=VERSION_ID,
        status=status,
        result=result,
        sections_total=6,
        sections_done=3,
        created_at=NOW,
        updated_at=NOW,
    )


def _service(**methods: AsyncMock) -> AsyncMock:
    svc = AsyncMock(spec=service.QualityJobService)
    for name, method in methods.items():
        setattr(svc, name, method)
    app.entrypoints.fastapi.app.dependency_overrides[
        dependencies.get_quality_job_service
    ] = lambda: svc
    return svc


def teardown_function() -> None:
    app.entrypoints.fastapi.app.dependency_overrides.clear()


class TestStartCheck:
    def test_accepts_a_check_and_returns_its_job(self) -> None:
        _service(start_check=AsyncMock(return_value=_job()))

        response = client.post(
            "/quality-checks/check", json={"documentId": str(DOCUMENT_ID)}
        )

        assert response.status_code == 202
        data = response.json()
        assert data["jobId"] == str(JOB_ID)
        assert data["documentId"] == str(DOCUMENT_ID)
        assert data["versionId"] == VERSION_ID
        assert data["status"] == "pending"
        assert data["result"] is None
        assert data["sectionsTotal"] == 6
        assert data["sectionsDone"] == 3

    def test_a_guide_that_does_not_exist_is_not_found(self) -> None:
        _service(start_check=AsyncMock(side_effect=service.GuideNotFoundError()))

        response = client.post(
            "/quality-checks/check", json={"documentId": str(DOCUMENT_ID)}
        )

        assert response.status_code == 404

    def test_a_document_id_that_is_not_a_uuid_is_refused(self) -> None:
        _service()

        response = client.post("/quality-checks/check", json={"documentId": "x"})

        assert response.status_code == 422


class TestGetJob:
    def test_returns_a_completed_job_with_its_comments(self) -> None:
        _service(
            get_job=AsyncMock(return_value=_job(models.JobStatus.COMPLETED, RESULT))
        )

        response = client.get(f"/quality-checks/jobs/{JOB_ID}")

        assert response.status_code == 200
        [comment] = response.json()["result"]["comments"]
        assert comment == {
            "commentId": str(COMMENT_ID),
            "ruleId": "GM-Qual-24",
            "ruleVersion": 1,
            "section": "2.1",
            "anchor": {
                "prefix": "Complete a review of CRM to ",
                "exact": "confirm is any correspondence",
                "suffix": " has been received.",
            },
            "issue": "'is' should be 'if'.",
            "whyItMatters": "The error makes the instruction harder to read.",
            "recommendation": "Change 'is' to 'if'.",
            "confidence": "high",
        }

    def test_an_unknown_job_is_not_found(self) -> None:
        _service(get_job=AsyncMock(return_value=None))

        response = client.get(f"/quality-checks/jobs/{JOB_ID}")

        assert response.status_code == 404


class TestGetLatest:
    def test_returns_the_latest_job_for_a_guide(self) -> None:
        svc = _service(
            get_latest_for_document=AsyncMock(
                return_value=_job(models.JobStatus.COMPLETED, RESULT)
            )
        )

        response = client.get(f"/quality-checks/documents/{DOCUMENT_ID}/latest")

        assert response.status_code == 200
        assert response.json()["jobId"] == str(JOB_ID)
        assert response.json()["result"]["documentTitle"] == "Land use guide"
        svc.get_latest_for_document.assert_awaited_once_with(DOCUMENT_ID)

    def test_a_guide_never_checked_has_no_latest(self) -> None:
        _service(get_latest_for_document=AsyncMock(return_value=None))

        response = client.get(f"/quality-checks/documents/{DOCUMENT_ID}/latest")

        assert response.status_code == 404
