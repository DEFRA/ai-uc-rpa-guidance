"""Tests for the critique API endpoint."""

from unittest.mock import MagicMock

import fastapi.testclient
import pytest_mock

import app.entrypoints.fastapi
from app.critique import api_schemas, models

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


def make_approved_result() -> MagicMock:
    result = MagicMock()
    result.output = models.CritiqueOutput(
        approved=True,
        findings=[],
        conformance=[
            models.ConformanceSummary(
                standard=models.Standard.GDS, summary="Conforms throughout"
            ),
            models.ConformanceSummary(
                standard=models.Standard.DEFRA_STYLE, summary="Conforms throughout"
            ),
        ],
        summary="Document meets the standards",
    )
    result.usage.input_tokens = 100
    result.usage.output_tokens = 50
    return result


class TestCritiqueEndpoint:
    def test_valid_document_returns_reports_and_revision(
        self, mocker: pytest_mock.MockerFixture
    ) -> None:
        mocker.patch(
            "app.critique.service.critic.critic_agent.run",
            return_value=make_approved_result(),
        )

        response = client.post(
            "/critique/analyse",
            json={"document_text": "# Test Document\n\nSome guidance."},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
        assert data["iterations"] == 1
        assert data["revised_document"] is None  # critique-only by default
        assert [r["standard"] for r in data["reports"]] == ["gds", "defra_style"]
        assert data["usage"] == {"input_tokens": 100, "output_tokens": 50}

    def test_max_iterations_is_passed_through(
        self, mocker: pytest_mock.MockerFixture
    ) -> None:
        critique_service = mocker.patch(
            "app.critique.router.service.critique_document",
            return_value=api_schemas.CritiqueResponse(
                status="approved",
                iterations=1,
                revised_document="# Doc",
            ),
        )

        client.post(
            "/critique/analyse",
            json={"document_text": "# Doc", "max_iterations": 2, "revise": True},
        )

        critique_service.assert_awaited_once_with(
            "# Doc", max_iterations=2, revise=True
        )

    def test_empty_document_is_rejected(self) -> None:
        response = client.post("/critique/analyse", json={"document_text": ""})

        assert response.status_code == 422

    def test_missing_document_text_is_rejected(self) -> None:
        response = client.post("/critique/analyse", json={})

        assert response.status_code == 422

    def test_zero_max_iterations_is_rejected(self) -> None:
        response = client.post(
            "/critique/analyse",
            json={"document_text": "# Doc", "max_iterations": 0},
        )

        assert response.status_code == 422
