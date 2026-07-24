"""Tests for manifest-driven section topology and content assembly."""

import uuid
from unittest.mock import AsyncMock

from app.guidance.documents import api_schemas, sectioning

_DOCUMENT_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _manifest(
    entries: list[tuple[str, str | None, list[str]]],
) -> api_schemas.DocumentManifestResponse:
    """Build a manifest from (number, parent, children) triples."""
    return api_schemas.DocumentManifestResponse(
        document_id=str(_DOCUMENT_ID),
        title="Test Document",
        sections=[
            api_schemas.ManifestSectionNodeResponse(
                number=number,
                heading=f"Heading {number}",
                level=number.count(".") + 1,
                parent=parent,
                children=children,
            )
            for number, parent, children in entries
        ],
    )


_NESTED = _manifest(
    [
        ("1", None, []),
        ("2", None, ["2.1", "2.2"]),
        ("2.1", "2", ["2.1.1"]),
        ("2.1.1", "2.1", []),
        ("2.2", "2", []),
        ("3", None, []),
    ]
)


class TestSectionAndDescendantNumbers:
    def test_walks_descendants_depth_first_in_document_order(self) -> None:
        result = sectioning.section_and_descendant_numbers(_NESTED, "2")

        assert result == ["2", "2.1", "2.1.1", "2.2"]

    def test_leaf_section_returns_itself_only(self) -> None:
        assert sectioning.section_and_descendant_numbers(_NESTED, "2.2") == ["2.2"]

    def test_unknown_number_falls_back_to_itself(self) -> None:
        assert sectioning.section_and_descendant_numbers(_NESTED, "9") == ["9"]


class TestTopLevelSectionNumbers:
    def test_returns_parentless_numbers_in_manifest_order(self) -> None:
        assert sectioning.top_level_section_numbers(_NESTED) == ["1", "2", "3"]

    def test_empty_manifest_yields_no_numbers(self) -> None:
        assert sectioning.top_level_section_numbers(_manifest([])) == []


class TestFetchJoinedSections:
    async def test_joins_sections_with_blank_line_and_trailing_newline(self) -> None:
        s3_repo = AsyncMock()
        s3_repo.download_section = AsyncMock(
            side_effect=["## 2 Middle\n\nB.\n", "### 2.1 Sub\n\nC."]
        )

        result = await sectioning.fetch_joined_sections(
            s3_repo, _DOCUMENT_ID, ["2", "2.1"]
        )

        assert result == "## 2 Middle\n\nB.\n\n### 2.1 Sub\n\nC.\n"
        s3_repo.download_section.assert_any_await(_DOCUMENT_ID, "2")
        s3_repo.download_section.assert_any_await(_DOCUMENT_ID, "2.1")
