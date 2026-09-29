"""Tests for the prototype guides API endpoints."""

import json
from unittest.mock import AsyncMock

import botocore.exceptions
import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.guidance.prototype import dependencies as prototype_dependencies

_DOCUMENT_ID = "2403b062-1ca7-4ef7-9df1-87669c51b281"
_V1_ID = "d37b0ccf-0000-0000-0000-000000000001"
_V2_ID = "08711078-0000-0000-0000-000000000002"

_MANIFEST = {
    "claims-guide": {
        "documentId": _DOCUMENT_ID,
        "title": "Claims Guide",
        "latestVersion": 2,
        "versions": [
            {
                "version": 1,
                "versionId": _V1_ID,
                "createdAt": "2026-09-28T16:54:37.963974+00:00",
                "sections": 1,
                "images": 0,
                "contentUrl": f"{_DOCUMENT_ID}/{_V1_ID}/content.md",
            },
            {
                "version": 2,
                "versionId": _V2_ID,
                "createdAt": "2026-09-28T17:00:00.000000+00:00",
                "sections": 2,
                "images": 1,
                "contentUrl": f"{_DOCUMENT_ID}/{_V2_ID}/content.md",
            },
        ],
    }
}
_MANIFEST_JSON = json.dumps(_MANIFEST)


def _no_such_key_error() -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        error_response={
            "Error": {"Code": "NoSuchKey", "Message": "The key does not exist"}
        },
        operation_name="GetObject",
    )


@pytest.fixture
def mock_s3_repo() -> AsyncMock:
    """Create a mock prototype guide S3 repository."""
    return AsyncMock()


@pytest.fixture
def client_with_s3(mock_s3_repo: AsyncMock) -> fastapi.testclient.TestClient:
    """Create a test client with a mocked S3 repository."""
    test_app = app.entrypoints.fastapi.app
    test_app.dependency_overrides[prototype_dependencies.get_s3_repository] = lambda: (
        mock_s3_repo
    )
    return fastapi.testclient.TestClient(test_app)


class TestManifestEndpoint:
    """Test GET /prototype/guides/manifest."""

    def test_returns_manifest(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()

        response = client_with_s3.get("/prototype/guides/manifest")

        assert response.status_code == 200
        data = response.json()
        assert data["claims-guide"]["documentId"] == _DOCUMENT_ID
        assert data["claims-guide"]["title"] == "Claims Guide"
        assert data["claims-guide"]["latestVersion"] == 2
        assert len(data["claims-guide"]["versions"]) == 2

    def test_returns_404_when_manifest_missing(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.side_effect = _no_such_key_error()

        response = client_with_s3.get("/prototype/guides/manifest")

        assert response.status_code == 404
        assert response.json()["detail"] == "Manifest not found"


class TestContentEndpoint:
    """Test GET /prototype/guides/{document_id}/content."""

    def test_returns_content_by_explicit_version_id_without_manifest(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_content.return_value = b"# Version 1"

        response = client_with_s3.get(
            f"/prototype/guides/{_DOCUMENT_ID}/content",
            params={"version_id": _V1_ID},
        )

        assert response.status_code == 200
        assert response.text == "# Version 1"
        mock_s3_repo.download_manifest.assert_not_called()
        mock_s3_repo.download_content.assert_awaited_once_with(_DOCUMENT_ID, _V1_ID)

    def test_resolves_latest_version_when_version_id_omitted(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()
        mock_s3_repo.download_content.return_value = b"# Version 2"

        response = client_with_s3.get(f"/prototype/guides/{_DOCUMENT_ID}/content")

        assert response.status_code == 200
        assert response.text == "# Version 2"
        mock_s3_repo.download_content.assert_awaited_once_with(_DOCUMENT_ID, _V2_ID)

    def test_returns_404_for_unknown_document_id(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()

        response = client_with_s3.get("/prototype/guides/unknown-doc-id/content")

        assert response.status_code == 404
        assert response.json()["detail"] == "No guide unknown-doc-id"
        mock_s3_repo.download_content.assert_not_called()

    def test_returns_404_when_content_object_missing(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_content.side_effect = _no_such_key_error()

        response = client_with_s3.get(
            f"/prototype/guides/{_DOCUMENT_ID}/content",
            params={"version_id": _V1_ID},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Content not found"


class TestAssetEndpoint:
    """Test GET /prototype/guides/{document_id}/assets/{asset_id}."""

    def test_returns_asset_bytes(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()
        mock_s3_repo.download_asset.return_value = b"\x89PNG\r\n"

        response = client_with_s3.get(
            f"/prototype/guides/{_DOCUMENT_ID}/assets/abcd1234.png",
            params={"version_id": _V1_ID},
        )

        assert response.status_code == 200
        assert response.content == b"\x89PNG\r\n"
        assert response.headers["content-type"] == "image/png"

    def test_same_asset_identical_across_versions_of_same_document(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()
        mock_s3_repo.download_asset.return_value = b"same-bytes"

        response_v1 = client_with_s3.get(
            f"/prototype/guides/{_DOCUMENT_ID}/assets/abcd1234.png",
            params={"version_id": _V1_ID},
        )
        response_v2 = client_with_s3.get(
            f"/prototype/guides/{_DOCUMENT_ID}/assets/abcd1234.png",
            params={"version_id": _V2_ID},
        )

        assert response_v1.content == response_v2.content == b"same-bytes"
        assert mock_s3_repo.download_asset.await_args_list == [
            ((_DOCUMENT_ID, "abcd1234.png"),),
            ((_DOCUMENT_ID, "abcd1234.png"),),
        ]

    def test_returns_404_for_unknown_document_id(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()

        response = client_with_s3.get(
            "/prototype/guides/unknown-doc-id/assets/abcd1234.png"
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "No guide unknown-doc-id"
        mock_s3_repo.download_asset.assert_not_called()

    def test_returns_404_for_version_not_belonging_to_document(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()

        response = client_with_s3.get(
            f"/prototype/guides/{_DOCUMENT_ID}/assets/abcd1234.png",
            params={"version_id": "not-a-real-version"},
        )

        assert response.status_code == 404
        mock_s3_repo.download_asset.assert_not_called()

    def test_returns_404_when_asset_object_missing(
        self, client_with_s3: fastapi.testclient.TestClient, mock_s3_repo: AsyncMock
    ) -> None:
        mock_s3_repo.download_manifest.return_value = _MANIFEST_JSON.encode()
        mock_s3_repo.download_asset.side_effect = _no_such_key_error()

        response = client_with_s3.get(
            f"/prototype/guides/{_DOCUMENT_ID}/assets/missing.png",
            params={"version_id": _V1_ID},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Asset not found"
