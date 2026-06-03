"""Tests for the publishing API endpoint."""

from unittest.mock import AsyncMock

import fastapi.testclient
import pytest

import app.entrypoints.fastapi

client = fastapi.testclient.TestClient(app.entrypoints.fastapi.app)


class TestAnalyseEndpoint:
    """Test the /publishing/analyse endpoint."""

    def test_analyse_with_valid_document(self, mocker: pytest.Mock) -> None:
        """Test analyzing a document with valid input."""
        mock_result = AsyncMock()
        mock_result.output.findings = []
        mock_result.output.summary = "Document is ready"
        mock_result.output.status = "completed"
        mock_result.usage.input_tokens = 100
        mock_result.usage.output_tokens = 50

        mocker.patch(
            "app.publishing.service.checker.checker_agent.run",
            return_value=mock_result,
        )

        response = client.post(
            "/publishing/analyse",
            json={"document_text": "# Test Document\n\nThis is a test."},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert isinstance(data["findings"], list)
        assert "summary" in data

    def test_analyse_with_empty_document(self) -> None:
        """Test that empty document text is rejected."""
        response = client.post(
            "/publishing/analyse",
            json={"document_text": ""},
        )

        assert response.status_code == 422

    def test_analyse_without_document_text(self) -> None:
        """Test that missing document_text is rejected."""
        response = client.post(
            "/publishing/analyse",
            json={},
        )

        assert response.status_code == 422

    def test_analyse_with_findings(self, mocker: pytest.Mock) -> None:
        """Test analyzing a document that returns findings."""
        mock_finding = AsyncMock()
        mock_finding.section = "Step 2"
        mock_finding.issue = "Unclear instruction"
        mock_finding.severity.value = "high"
        mock_finding.recommendation = "Clarify the steps"

        mock_result = AsyncMock()
        mock_result.output.findings = [mock_finding]
        mock_result.output.summary = "Document has issues"
        mock_result.output.status = "completed"
        mock_result.usage.input_tokens = 150
        mock_result.usage.output_tokens = 100

        mocker.patch(
            "app.publishing.service.checker.checker_agent.run",
            return_value=mock_result,
        )

        response = client.post(
            "/publishing/analyse",
            json={"document_text": "# Problematic Document\n\nContent here."},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["findings"]) == 1
        assert data["findings"][0]["section"] == "Step 2"
        assert data["findings"][0]["severity"] == "high"


class TestSwaggerDocumentation:
    """Test that API documentation is available."""

    def test_swagger_docs_available(self) -> None:
        """Test that Swagger docs are accessible at /docs."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_schema_available(self) -> None:
        """Test that OpenAPI schema includes publishing endpoints."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "/publishing/analyse" in data["paths"]
