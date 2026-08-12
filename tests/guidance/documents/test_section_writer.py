"""Applying an editor's correction to a single parsed guidance section."""

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.guidance.documents import s3_repository, section_writer
from app.guidance.pipeline.models import InlineSpan, ParagraphNode, SectionNode
from app.guidance.pipeline.renderers.markdown import section_to_markdown

DOCUMENT_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")

MANIFEST = {
    "document_id": str(DOCUMENT_ID),
    "title": "SFI Parcel Guidance",
    "sections": [
        {
            "number": "1",
            "heading": "Overview",
            "level": 1,
            "parent": None,
            "children": ["1.1"],
            "links": [],
        },
        {
            "number": "1.1",
            "heading": "Detail",
            "level": 2,
            "parent": "1",
            "children": [],
            "links": [],
        },
        {
            "number": "2",
            "heading": "Conclusion",
            "level": 1,
            "parent": None,
            "children": [],
            "links": [],
        },
    ],
}

SECTION_FILES = {
    "1": "## 1 Overview\n\nIntro text.\n",
    "1.1": "### 1.1 Detail\n\nDetail text.\n",
    "2": "## 2 Conclusion\n\nDone.\n",
}


@pytest.fixture
def mock_s3_repo() -> AsyncMock:
    """A storage repository primed with a three-section document."""
    repo = AsyncMock(spec=s3_repository.AbstractGuidanceStorageRepository)
    repo.download_manifest.return_value = json.dumps(MANIFEST)

    stored = dict(SECTION_FILES)

    async def download_section(_document_id: uuid.UUID, number: str) -> str:
        return stored[number]

    async def upload_section(
        _document_id: uuid.UUID, number: str, markdown: str
    ) -> None:
        stored[number] = markdown

    repo.download_section.side_effect = download_section
    repo.upload_section.side_effect = upload_section
    return repo


def uploaded_manifest(mock_s3_repo: AsyncMock) -> dict:
    """The manifest JSON handed to storage, parsed back."""
    return json.loads(mock_s3_repo.upload_manifest.await_args.args[1])


def uploaded_content(mock_s3_repo: AsyncMock) -> str:
    """The regenerated content.md handed to storage."""
    return str(mock_s3_repo.upload_content.await_args.args[1])


class TestSectionFileComposition:
    """The stored section file must look exactly like a parsed one."""

    async def test_writes_heading_line_from_the_manifest(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """Number and level come from the manifest, never from the client."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body="Corrected text."
        )

        mock_s3_repo.upload_section.assert_awaited_once_with(
            DOCUMENT_ID, "1", "## 1 Overview\n\nCorrected text.\n"
        )

    async def test_heading_level_is_offset_by_one(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """Level 2 becomes ###, because # is reserved for the document title."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1.1", heading="Detail", body="More detail."
        )

        mock_s3_repo.upload_section.assert_awaited_once_with(
            DOCUMENT_ID, "1.1", "### 1.1 Detail\n\nMore detail.\n"
        )

    async def test_renamed_heading_appears_in_the_section_file(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """The heading is editable even though the number is not."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Introduction", body="Text."
        )

        mock_s3_repo.upload_section.assert_awaited_once_with(
            DOCUMENT_ID, "1", "## 1 Introduction\n\nText.\n"
        )

    async def test_empty_body_yields_heading_only(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """Matches what the parser emits for a section with no content."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body=""
        )

        mock_s3_repo.upload_section.assert_awaited_once_with(
            DOCUMENT_ID, "1", "## 1 Overview\n"
        )

    async def test_normalises_windows_line_endings(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """An HTML textarea posts CRLF; stored markdown must stay LF-only."""
        await section_writer.update_section(
            mock_s3_repo,
            DOCUMENT_ID,
            "1",
            heading="Overview",
            body="First line.\r\n\r\nSecond line.",
        )

        written = mock_s3_repo.upload_section.await_args.args[2]
        assert "\r" not in written
        assert written == "## 1 Overview\n\nFirst line.\n\nSecond line.\n"

    async def test_trims_surrounding_blank_lines_from_the_body(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """Otherwise the file accumulates blank lines on every save."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body="\n\nText.\n\n\n"
        )

        mock_s3_repo.upload_section.assert_awaited_once_with(
            DOCUMENT_ID, "1", "## 1 Overview\n\nText.\n"
        )

    async def test_preserves_a_trailing_non_breaking_space(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """U+00A0 is content, not layout, and Word documents are full of it.

        Python's str.strip() treats it as whitespace, which would silently delete
        a character the author put there.
        """
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body="Text. "
        )

        mock_s3_repo.upload_section.assert_awaited_once_with(
            DOCUMENT_ID, "1", "## 1 Overview\n\nText. \n"
        )

    async def test_saving_unchanged_content_is_idempotent(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """Re-saving a section as-is must not drift the stored bytes."""
        original = SECTION_FILES["1"]
        _heading_line, body = original.split("\n\n", 1)

        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body=body
        )

        assert mock_s3_repo.upload_section.await_args.args[2] == original

    async def test_matches_parser_output_byte_for_byte(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """An edited section must be indistinguishable from an imported one.

        Renders a section through the real pipeline renderer, feeds its body back
        through the write path, and requires identical bytes.
        """
        node = SectionNode(
            heading="Overview",
            level=1,
            number="1",
            content=[ParagraphNode(spans=[InlineSpan(text="Intro text.")])],
        )
        expected = section_to_markdown(node)
        _heading_line, body = expected.split("\n\n", 1)

        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body=body
        )

        assert mock_s3_repo.upload_section.await_args.args[2] == expected


class TestManifestUpdate:
    """The manifest is the source of truth for headings and the table of contents."""

    async def test_updates_the_edited_section_heading(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Introduction", body="Text."
        )

        sections = {s["number"]: s for s in uploaded_manifest(mock_s3_repo)["sections"]}
        assert sections["1"]["heading"] == "Introduction"

    async def test_leaves_other_sections_untouched(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Introduction", body="Text."
        )

        sections = {s["number"]: s for s in uploaded_manifest(mock_s3_repo)["sections"]}
        assert sections["1.1"]["heading"] == "Detail"
        assert sections["2"]["heading"] == "Conclusion"

    async def test_preserves_snake_case_keys_and_topology(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """The parser writes snake_case; a differently-shaped manifest would not parse."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Introduction", body="Text."
        )

        manifest = uploaded_manifest(mock_s3_repo)
        assert manifest["document_id"] == str(DOCUMENT_ID)
        assert manifest["title"] == "SFI Parcel Guidance"
        assert manifest["sections"] == [
            {**MANIFEST["sections"][0], "heading": "Introduction"},
            MANIFEST["sections"][1],
            MANIFEST["sections"][2],
        ]


class TestContentRegeneration:
    """content.md is a derived artefact that the review checker reads directly."""

    async def test_regenerates_content_from_all_sections_in_order(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body="Corrected text."
        )

        assert uploaded_content(mock_s3_repo) == (
            "# SFI Parcel Guidance\n\n"
            "## 1 Overview\n\nCorrected text.\n\n"
            "### 1.1 Detail\n\nDetail text.\n\n"
            "## 2 Conclusion\n\nDone.\n"
        )

    async def test_content_reflects_the_edit_not_the_stale_section(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """Regression guard for the review checker reading pre-edit text."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1.1", heading="Detail", body="Fixed SBI."
        )

        content = uploaded_content(mock_s3_repo)
        assert "Fixed SBI." in content
        assert "Detail text." not in content

    async def test_regenerates_after_writing_the_section(
        self, mock_s3_repo: AsyncMock
    ) -> None:
        """Ordering matters: content.md is assembled by re-reading section files."""
        await section_writer.update_section(
            mock_s3_repo, DOCUMENT_ID, "1", heading="Overview", body="New text."
        )

        assert "New text." in uploaded_content(mock_s3_repo)


class TestUnknownSection:
    """A number absent from the manifest is a 404, not a new section."""

    async def test_raises_section_not_found(self, mock_s3_repo: AsyncMock) -> None:
        with pytest.raises(section_writer.SectionNotFoundError):
            await section_writer.update_section(
                mock_s3_repo, DOCUMENT_ID, "9.9", heading="Nope", body="Text."
            )

    async def test_writes_nothing(self, mock_s3_repo: AsyncMock) -> None:
        """Refuse before mutating, so a bad number cannot create an orphan file."""
        with pytest.raises(section_writer.SectionNotFoundError):
            await section_writer.update_section(
                mock_s3_repo, DOCUMENT_ID, "9.9", heading="Nope", body="Text."
            )

        mock_s3_repo.upload_section.assert_not_awaited()
        mock_s3_repo.upload_manifest.assert_not_awaited()
        mock_s3_repo.upload_content.assert_not_awaited()
