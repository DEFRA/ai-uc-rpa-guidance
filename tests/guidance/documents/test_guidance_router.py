"""Tests for the guidance API endpoints."""

from unittest.mock import AsyncMock

import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.guidance.documents import api_schemas
from app.guidance.documents import dependencies as guidance_dependencies


@pytest.fixture
def mock_guidance_service() -> AsyncMock:
    """Create a mock guidance service."""
    return AsyncMock()


@pytest.fixture
def client_with_mocks(
    mock_guidance_service: AsyncMock,
) -> fastapi.testclient.TestClient:
    """Create a test client with mocked guidance service."""
    test_app = app.entrypoints.fastapi.app
    test_app.dependency_overrides[guidance_dependencies.get_guidance_service] = lambda: (
        mock_guidance_service
    )
    return fastapi.testclient.TestClient(test_app)


class TestInitiateEndpoint:
    """Test the POST /guidance/documents endpoint."""

    def test_initiate_success(
        self,
        client_with_mocks: fastapi.testclient.TestClient,
        mock_guidance_service: AsyncMock,
    ) -> None:
        """Test initiating a document upload successfully."""
        upload_id = "507f1f77bcf86cd799439011"
        mock_guidance_service.initiate_upload.return_value = upload_id

        response = client_with_mocks.post(
            "/guidance/documents",
            json={
                "title": "Test Document",
                "description": "A test document upload",
                "redirect": "http://example.com/return",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert "uploadId" in data
        assert data["uploadId"] == upload_id

    def test_initiate_without_title(
        self,
        client_with_mocks: fastapi.testclient.TestClient,
        mock_guidance_service: AsyncMock,
    ) -> None:
        """Title is optional — inferred from document during parsing."""
        upload_id = "507f191e810c19729de860ea"
        mock_guidance_service.initiate_upload.return_value = upload_id

        response = client_with_mocks.post(
            "/guidance/documents",
            json={
                "description": "No title provided",
                "redirect": "http://example.com/return",
            },
        )

        assert response.status_code == 201

    def test_initiate_with_missing_redirect(
        self, client_with_mocks: fastapi.testclient.TestClient
    ) -> None:
        """Test initiate without required redirect field."""
        response = client_with_mocks.post(
            "/guidance/documents",
            json={
                "title": "Test",
                "description": "Missing redirect",
            },
        )

        assert response.status_code == 422

    def test_initiate_with_service_error(
        self,
        client_with_mocks: fastapi.testclient.TestClient,
        mock_guidance_service: AsyncMock,
    ) -> None:
        """Test initiate when service raises an exception."""
        mock_guidance_service.initiate_upload.side_effect = Exception(
            "CDP uploader error"
        )

        response = client_with_mocks.post(
            "/guidance/documents",
            json={
                "title": "Test Document",
                "description": "A test document upload",
                "redirect": "http://example.com/return",
            },
        )

        assert response.status_code == 502


class TestCallbackEndpoint:
    """Test the POST /guidance/documents/{document_id}/callback endpoint."""

    def test_callback_with_valid_payload(
        self,
        client_with_mocks: fastapi.testclient.TestClient,
        mock_guidance_service: AsyncMock,
    ) -> None:
        """Test callback handler with valid CDP uploader payload."""
        mock_guidance_service.handle_callback.return_value = None

        document_id = "12345678-1234-5678-1234-567812345678"
        response = client_with_mocks.post(
            f"/guidance/documents/{document_id}/callback",
            json={
                "uploadStatus": "completed",
                "form": {
                    "file1": {
                        "fileId": "file-1",
                        "filename": "test.pdf",
                        "fileStatus": "completed",
                        "contentLength": 1024,
                        "checksumSha256": "abc123",
                        "s3Key": "test.pdf",
                        "s3Bucket": "guidance-bucket",
                    }
                },
            },
        )

        assert response.status_code == 204

    def test_callback_with_invalid_document_id(
        self, client_with_mocks: fastapi.testclient.TestClient
    ) -> None:
        """Test callback with a non-UUID document ID is rejected at the router level."""
        response = client_with_mocks.post(
            "/guidance/documents/not-a-uuid/callback",
            json={
                "uploadStatus": "completed",
                "form": {},
            },
        )

        assert response.status_code == 422

    def test_callback_with_nonexistent_document(
        self,
        client_with_mocks: fastapi.testclient.TestClient,
        mock_guidance_service: AsyncMock,
    ) -> None:
        """Test callback with document ID that doesn't exist."""
        mock_guidance_service.handle_callback.side_effect = ValueError(
            "Document not found"
        )

        document_id = "12345678-1234-5678-1234-567812345678"
        response = client_with_mocks.post(
            f"/guidance/documents/{document_id}/callback",
            json={
                "uploadStatus": "completed",
                "form": {},
            },
        )

        assert response.status_code == 404


class TestListDocumentsEndpoint:
    """Test the GET /guidance/documents endpoint."""

    def test_list_documents_success(
        self,
        client_with_mocks: fastapi.testclient.TestClient,
        mock_guidance_service: AsyncMock,
    ) -> None:
        """Test listing documents with pagination."""
        mock_guidance_service.list_documents.return_value = (
            api_schemas.DocumentListResponse(
                items=[],
                total=0,
                page=1,
                page_size=10,
            )
        )

        response = client_with_mocks.get("/guidance/documents?page=1&page_size=10")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "pageSize" in data
        assert data["page"] == 1
        assert data["pageSize"] == 10

    def test_list_documents_with_invalid_page(
        self, client_with_mocks: fastapi.testclient.TestClient
    ) -> None:
        """Test that invalid page number is rejected."""
        response = client_with_mocks.get("/guidance/documents?page=0&page_size=10")

        assert response.status_code == 422

    def test_list_documents_with_invalid_page_size(
        self, client_with_mocks: fastapi.testclient.TestClient
    ) -> None:
        """Test that invalid page size is rejected."""
        response = client_with_mocks.get("/guidance/documents?page=1&page_size=1000")

        assert response.status_code == 422

    def test_list_documents_with_defaults(
        self,
        client_with_mocks: fastapi.testclient.TestClient,
        mock_guidance_service: AsyncMock,
    ) -> None:
        """Test listing documents with default pagination parameters."""
        mock_guidance_service.list_documents.return_value = (
            api_schemas.DocumentListResponse(
                items=[],
                total=0,
                page=1,
                page_size=10,
            )
        )

        response = client_with_mocks.get("/guidance/documents")

        assert response.status_code == 200


class TestSwaggerDocumentation:
    """Test that API documentation is available."""

    def test_swagger_docs_available(
        self, client_with_mocks: fastapi.testclient.TestClient
    ) -> None:
        """Test that Swagger docs are accessible at /docs."""
        response = client_with_mocks.get("/docs")
        assert response.status_code == 200

    def test_openapi_schema_includes_guidance_endpoints(
        self, client_with_mocks: fastapi.testclient.TestClient
    ) -> None:
        """Test that OpenAPI schema includes guidance endpoints."""
        response = client_with_mocks.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "/guidance/documents/" in data["paths"]
