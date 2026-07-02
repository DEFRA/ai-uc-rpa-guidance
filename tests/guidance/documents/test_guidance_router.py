"""Tests for the guidance API endpoints."""

from unittest.mock import AsyncMock

import botocore.exceptions
import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.guidance.documents import api_schemas, s3_repository
from app.guidance.documents import dependencies as guidance_dependencies


def _no_such_key_error() -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        error_response={
            "Error": {"Code": "NoSuchKey", "Message": "The key does not exist"}
        },
        operation_name="GetObject",
    )


@pytest.fixture
def mock_guidance_service() -> AsyncMock:
    """Create a mock guidance service."""
    return AsyncMock()


@pytest.fixture
def mock_s3_repo() -> AsyncMock:
    """Create a mock S3 repository."""
    return AsyncMock(spec=s3_repository.AbstractGuidanceStorageRepository)


@pytest.fixture
def client_with_mocks(
    mock_guidance_service: AsyncMock,
) -> fastapi.testclient.TestClient:
    """Create a test client with mocked guidance service."""
    test_app = app.entrypoints.fastapi.app
    test_app.dependency_overrides[guidance_dependencies.get_guidance_service] = (
        lambda: (mock_guidance_service)
    )
    return fastapi.testclient.TestClient(test_app)


@pytest.fixture
def client_with_s3(
    mock_s3_repo: AsyncMock,
) -> fastapi.testclient.TestClient:
    """Create a test client with a mocked S3 repository."""
    test_app = app.entrypoints.fastapi.app
    test_app.dependency_overrides[guidance_dependencies.get_s3_repository] = lambda: (
        mock_s3_repo
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


class TestManifestEndpoint:
    """Test GET /guidance/documents/{document_id}/manifest."""

    _DOCUMENT_ID = "12345678-1234-5678-1234-567812345678"
    _MANIFEST_JSON = (
        '{"document_id": "12345678-1234-5678-1234-567812345678", "title": "Test", "sections": ['
        '{"number": "1", "heading": "Intro", "level": 1, "parent": null, "children": [], "links": []}'
        "]}"
    )

    def test_returns_manifest(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        mock_s3_repo.download_manifest.return_value = self._MANIFEST_JSON

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/manifest"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["documentId"] == self._DOCUMENT_ID
        assert len(data["sections"]) == 1
        assert data["sections"][0]["number"] == "1"

    def test_returns_404_when_manifest_missing(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        mock_s3_repo.download_manifest.side_effect = _no_such_key_error()

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/manifest"
        )

        assert response.status_code == 404

    def test_returns_422_for_invalid_document_id(
        self,
        client_with_s3: fastapi.testclient.TestClient,
    ) -> None:
        response = client_with_s3.get("/guidance/documents/not-a-uuid/manifest")

        assert response.status_code == 422


class TestSectionEndpoint:
    """Test GET /guidance/documents/{document_id}/sections/{section_number}."""

    _DOCUMENT_ID = "12345678-1234-5678-1234-567812345678"

    def test_returns_section_markdown(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        mock_s3_repo.download_section.return_value = "## 1 Intro\n\nContent here."

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/sections/1"
        )

        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        assert "## 1 Intro" in response.text

    def test_returns_section_with_dotted_number(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        mock_s3_repo.download_section.return_value = "### 1.2.3 Deep\n\nContent."

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/sections/1.2.3"
        )

        assert response.status_code == 200

    def test_returns_404_when_section_missing(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        mock_s3_repo.download_section.side_effect = _no_such_key_error()

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/sections/99"
        )

        assert response.status_code == 404

    def test_returns_422_for_invalid_section_number(
        self,
        client_with_s3: fastapi.testclient.TestClient,
    ) -> None:
        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/sections/not-a-number"
        )

        assert response.status_code == 422

    def test_returns_422_for_section_with_slash(
        self,
        client_with_s3: fastapi.testclient.TestClient,
    ) -> None:
        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/sections/1/2"
        )

        assert response.status_code in {404, 422}


class TestImageEndpoint:
    """Test GET /guidance/documents/{document_id}/images/{filename}."""

    _DOCUMENT_ID = "12345678-1234-5678-1234-567812345678"

    def test_returns_image_bytes(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        image_data = b"\x89PNG\r\n\x1a\n"
        mock_s3_repo.download_image.return_value = image_data

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/images/img_1.png"
        )

        assert response.status_code == 200
        assert response.content == image_data
        assert response.headers["content-type"] == "image/png"

    def test_returns_correct_content_type_for_jpeg(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        mock_s3_repo.download_image.return_value = b"jpeg_data"

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/images/img_2.jpeg"
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_returns_404_when_image_missing(
        self,
        client_with_s3: fastapi.testclient.TestClient,
        mock_s3_repo: AsyncMock,
    ) -> None:
        mock_s3_repo.download_image.side_effect = _no_such_key_error()

        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/images/img_1.png"
        )

        assert response.status_code == 404

    def test_returns_422_for_filename_starting_with_dash(
        self,
        client_with_s3: fastapi.testclient.TestClient,
    ) -> None:
        response = client_with_s3.get(
            f"/guidance/documents/{self._DOCUMENT_ID}/images/-secret.png"
        )

        assert response.status_code == 422

    def test_returns_422_for_invalid_document_id(
        self,
        client_with_s3: fastapi.testclient.TestClient,
    ) -> None:
        response = client_with_s3.get("/guidance/documents/not-a-uuid/images/img_1.png")

        assert response.status_code == 422


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
