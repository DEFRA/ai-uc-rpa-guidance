"""Validation rules for the section update request body.

Covered here rather than through the router because the size caps are
impractical to exercise over HTTP.
"""

import pydantic
import pytest

from app.guidance.documents import api_schemas


class TestSectionUpdateRequest:
    """Constraints on an editor-supplied section heading and body."""

    def test_accepts_a_heading_and_body(self) -> None:
        """The ordinary case: some heading text and some markdown."""
        request = api_schemas.SectionUpdateRequest(
            heading="Email — case note template", markdown="Some corrected text."
        )

        assert request.heading == "Email — case note template"
        assert request.markdown == "Some corrected text."

    def test_accepts_an_empty_body(self) -> None:
        """A section may legitimately have a heading and no content."""
        request = api_schemas.SectionUpdateRequest(heading="Overview", markdown="")

        assert request.markdown == ""

    def test_strips_surrounding_whitespace_from_the_heading(self) -> None:
        """The heading is composed into a single markdown line, so padding would break it."""
        request = api_schemas.SectionUpdateRequest(
            heading="  Overview  ", markdown="Body."
        )

        assert request.heading == "Overview"

    def test_rejects_an_empty_heading(self) -> None:
        """A section always has a heading; blanking it would corrupt the manifest."""
        with pytest.raises(pydantic.ValidationError):
            api_schemas.SectionUpdateRequest(heading="", markdown="Body.")

    def test_rejects_a_whitespace_only_heading(self) -> None:
        """Whitespace is stripped first, so this is an empty heading."""
        with pytest.raises(pydantic.ValidationError):
            api_schemas.SectionUpdateRequest(heading="   ", markdown="Body.")

    def test_rejects_a_heading_containing_a_newline(self) -> None:
        """A newline would split the composed heading line and invent a section."""
        with pytest.raises(pydantic.ValidationError):
            api_schemas.SectionUpdateRequest(
                heading="Overview\n## 2 Injected", markdown="Body."
            )

    def test_rejects_an_over_long_heading(self) -> None:
        """Bounded so a heading cannot be used to store a document."""
        with pytest.raises(pydantic.ValidationError):
            api_schemas.SectionUpdateRequest(
                heading="x" * (api_schemas.MAX_HEADING_LENGTH + 1), markdown="Body."
            )

    def test_accepts_a_heading_at_the_limit(self) -> None:
        """The cap is inclusive."""
        heading = "x" * api_schemas.MAX_HEADING_LENGTH

        request = api_schemas.SectionUpdateRequest(heading=heading, markdown="Body.")

        assert request.heading == heading

    def test_rejects_an_over_long_body(self) -> None:
        """Bounded to keep a single section from exhausting memory on read-back."""
        with pytest.raises(pydantic.ValidationError):
            api_schemas.SectionUpdateRequest(
                heading="Overview", markdown="x" * (api_schemas.MAX_MARKDOWN_LENGTH + 1)
            )

    def test_requires_both_fields(self) -> None:
        """Neither field has a default: a partial update would silently blank the other."""
        with pytest.raises(pydantic.ValidationError):
            api_schemas.SectionUpdateRequest(heading="Overview")  # type: ignore[call-arg]
