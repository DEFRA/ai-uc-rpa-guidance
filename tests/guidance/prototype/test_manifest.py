"""Tests for the pure manifest version-resolution logic."""

from typing import Any

from app.guidance.prototype import manifest

_DOCUMENT_ID = "2403b062-1ca7-4ef7-9df1-87669c51b281"
_V1_ID = "d37b0ccf-0000-0000-0000-000000000001"
_V2_ID = "08711078-0000-0000-0000-000000000002"

_MANIFEST: dict[str, Any] = {
    "claims-guide": {
        "documentId": _DOCUMENT_ID,
        "latestVersion": 2,
        "versions": [
            {
                "version": 1,
                "versionId": _V1_ID,
                "createdAt": "2026-09-28T16:54:37.963974+00:00",
                "title": "Claims Guide",
                "sections": 1,
                "images": 0,
                "contentUrl": f"{_DOCUMENT_ID}/{_V1_ID}/content.md",
            },
            {
                "version": 2,
                "versionId": _V2_ID,
                "createdAt": "2026-09-28T17:00:00.000000+00:00",
                "title": "Claims Guide",
                "sections": 2,
                "images": 1,
                "contentUrl": f"{_DOCUMENT_ID}/{_V2_ID}/content.md",
            },
        ],
    }
}


class TestResolveVersionId:
    """Test manifest.resolve_version_id."""

    def test_resolves_latest_version_when_omitted(self) -> None:
        assert manifest.resolve_version_id(_MANIFEST, _DOCUMENT_ID) == _V2_ID

    def test_resolves_explicit_version(self) -> None:
        assert manifest.resolve_version_id(_MANIFEST, _DOCUMENT_ID, _V1_ID) == _V1_ID

    def test_unknown_document_id_returns_none(self) -> None:
        assert manifest.resolve_version_id(_MANIFEST, "unknown-doc-id") is None

    def test_unknown_document_id_with_explicit_version_returns_none(self) -> None:
        assert manifest.resolve_version_id(_MANIFEST, "unknown-doc-id", _V1_ID) is None

    def test_version_id_not_belonging_to_document_returns_none(self) -> None:
        assert (
            manifest.resolve_version_id(_MANIFEST, _DOCUMENT_ID, "not-a-version-id")
            is None
        )

    def test_empty_manifest_returns_none(self) -> None:
        assert manifest.resolve_version_id({}, _DOCUMENT_ID) is None
