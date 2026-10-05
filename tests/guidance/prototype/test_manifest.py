"""Tests for the pure manifest building and version-resolution logic."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.guidance.prototype import api_schemas, manifest

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


_AT = datetime(2026, 10, 5, 13, 0, 0, tzinfo=UTC)


def _version(
    version_id: str,
    markdown: str = "# Claims Guide\n",
    modified: datetime = _AT,
    document_id: str = _DOCUMENT_ID,
) -> manifest.GuideVersion:
    return manifest.GuideVersion(
        document_id=document_id,
        version_id=version_id,
        modified=modified,
        markdown=markdown,
    )


class TestBuild:
    """Test building the manifest from the versions found in a zip."""

    def test_builds_a_guide_with_one_version_in_the_existing_shape(self) -> None:
        markdown = (
            "# Claims Guide\n\n## 1 Scope\n\n![](../assets/a.png)\n\n"
            "### 1.1 Who\n\n![](../assets/b.png) and ![](../assets/a.png)\n"
        )

        built = manifest.build([_version(_V1_ID, markdown)])

        assert built == {
            "claims-guide": {
                "documentId": _DOCUMENT_ID,
                "title": "Claims Guide",
                "createdAt": "2026-10-05T13:00:00+00:00",
                "updatedAt": "2026-10-05T13:00:00+00:00",
                "latestVersion": 1,
                "versions": [
                    {
                        "version": 1,
                        "versionId": _V1_ID,
                        "createdAt": "2026-10-05T13:00:00+00:00",
                        "updatedAt": "2026-10-05T13:00:00+00:00",
                        "sections": 2,
                        "images": 3,
                        "contentUrl": f"{_DOCUMENT_ID}/{_V1_ID}/content.md",
                    }
                ],
            }
        }

    def test_validates_against_the_api_schema(self) -> None:
        built = manifest.build([_version(_V1_ID)])

        api_schemas.PrototypeGuide.model_validate(built["claims-guide"])

    def test_orders_versions_by_when_they_were_written(self) -> None:
        later = _AT + timedelta(days=1)
        built = manifest.build(
            [
                _version(_V1_ID, "# Renamed Guide\n", modified=later),
                _version(_V2_ID, "# Claims Guide\n", modified=_AT),
            ]
        )

        guide = built["renamed-guide"]
        assert [(v["version"], v["versionId"]) for v in guide["versions"]] == [
            (1, _V2_ID),
            (2, _V1_ID),
        ]
        assert guide["latestVersion"] == 2
        assert guide["title"] == "Renamed Guide"
        assert guide["createdAt"] == _AT.isoformat()
        assert guide["updatedAt"] == later.isoformat()
        assert manifest.resolve_version_id(built, _DOCUMENT_ID) == _V1_ID

    def test_orders_versions_written_at_once_by_id_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        built = manifest.build([_version(_V1_ID), _version(_V2_ID)])

        versions = built["claims-guide"]["versions"]
        assert [v["versionId"] for v in versions] == sorted([_V1_ID, _V2_ID])
        assert "share a timestamp" in caplog.text
        assert _DOCUMENT_ID in caplog.text

    def test_keys_guides_by_title_slug_made_unique(self) -> None:
        other = "5a1f0000-0000-0000-0000-000000000000"
        built = manifest.build(
            [
                _version(_V1_ID, "# CS MA Claim – Admin (2026)\n"),
                _version(_V2_ID, "# CS MA Claim: Admin 2026\n", document_id=other),
            ]
        )

        assert sorted(built) == ["cs-ma-claim-admin-2026", "cs-ma-claim-admin-2026-2"]

    def test_keys_an_untitled_guide_by_its_document_id(self) -> None:
        built = manifest.build([_version(_V1_ID, "No title line\n## 1 Scope\n")])

        assert built[_DOCUMENT_ID]["title"] == ""
        assert built[_DOCUMENT_ID]["versions"][0]["sections"] == 1

    def test_builds_nothing_from_no_versions(self) -> None:
        assert manifest.build([]) == {}
