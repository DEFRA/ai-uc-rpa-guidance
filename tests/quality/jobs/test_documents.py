"""Tests for PrototypeGuideContentSource."""

import json
import uuid
from unittest.mock import AsyncMock

import botocore.exceptions

from app.quality.jobs import documents

DOCUMENT_ID = uuid.UUID("48b8a1df-c71b-4de3-ac73-7c672aaf5456")
VERSION_ID = "ad1680a5-b12f-4b1f-9b7d-6ec1539c6fdf"

MANIFEST = {
    "land-use-guide": {
        "documentId": str(DOCUMENT_ID),
        "title": "Land use guide",
        "latestVersion": 1,
        "versions": [{"version": 1, "versionId": VERSION_ID}],
    }
}

CONTENT = (
    "# Land use guide\n\n## 1 Introduction\n\nRead this.\n\n## 2 Assign\n\nAssign.\n"
)


def _no_such_key() -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject"
    )


def _make_source(
    manifest: AsyncMock | None = None,
) -> tuple[documents.PrototypeGuideContentSource, AsyncMock]:
    s3_repo = AsyncMock()
    s3_repo.download_manifest = manifest or AsyncMock(
        return_value=json.dumps(MANIFEST).encode()
    )
    s3_repo.download_content = AsyncMock(return_value=CONTENT.encode())
    return documents.PrototypeGuideContentSource(s3_repo), s3_repo


class TestPrototypeGuideContentSource:
    async def test_loads_the_latest_version_of_a_guide(self) -> None:
        source, s3_repo = _make_source()

        content = await source.get(DOCUMENT_ID)

        assert content is not None
        assert content.document_id == DOCUMENT_ID
        assert content.version_id == VERSION_ID
        assert content.title == "Land use guide"
        s3_repo.download_content.assert_awaited_once_with(str(DOCUMENT_ID), VERSION_ID)

    async def test_cuts_the_guide_into_its_top_level_parts(self) -> None:
        source, _ = _make_source()

        content = await source.get(DOCUMENT_ID)

        assert content is not None
        assert [part.label for part in content.parts] == ["1", "2"]

    async def test_a_guide_not_in_the_manifest_is_not_found(self) -> None:
        source, _ = _make_source()

        assert await source.get(uuid.uuid4()) is None

    async def test_no_manifest_means_no_guide_is_found(self) -> None:
        source, _ = _make_source(AsyncMock(side_effect=_no_such_key()))

        assert await source.get(DOCUMENT_ID) is None
