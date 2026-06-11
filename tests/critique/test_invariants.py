"""Tests for the AC5 structural-drift invariant checks."""

from app.critique import invariants

ORIGINAL = """# Guide

## Step 1

Do the first thing. See the [navigation guide](https://example.com/nav.docx).

![screenshot](../images/img_1.png)

## Step 2

- **Yes**: continue.
- **No**: stop.

### Detail

More [guidance](https://example.com/guide.docx) here.
"""


class TestNoDrift:
    def test_identical_documents_produce_no_warnings(self) -> None:
        assert invariants.check_invariants(ORIGINAL, ORIGINAL) == []

    def test_text_level_rewording_produces_no_warnings(self) -> None:
        revised = ORIGINAL.replace("Do the first thing", "Do this first").replace(
            "## Step 2", "## Second step"
        )

        assert invariants.check_invariants(ORIGINAL, revised) == []


class TestImageDrift:
    def test_dropped_image_is_reported(self) -> None:
        revised = ORIGINAL.replace("![screenshot](../images/img_1.png)\n\n", "")

        warnings = invariants.check_invariants(ORIGINAL, revised)

        assert any(
            "Image reference missing" in w and "../images/img_1.png" in w
            for w in warnings
        )

    def test_altered_image_url_is_reported(self) -> None:
        revised = ORIGINAL.replace("img_1.png", "img_2.png")

        warnings = invariants.check_invariants(ORIGINAL, revised)

        assert any("img_1.png" in w for w in warnings)


class TestLinkDrift:
    def test_dropped_link_is_reported(self) -> None:
        revised = ORIGINAL.replace(
            "[navigation guide](https://example.com/nav.docx)", "navigation guide"
        )

        warnings = invariants.check_invariants(ORIGINAL, revised)

        assert any(
            "Link URL missing" in w and "https://example.com/nav.docx" in w
            for w in warnings
        )

    def test_duplicate_links_reduced_in_count_is_reported(self) -> None:
        original = "[a](https://example.com/x) and [b](https://example.com/x)"
        revised = "[a](https://example.com/x)"

        warnings = invariants.check_invariants(original, revised)

        assert any("https://example.com/x" in w and "(1x)" in w for w in warnings)

    def test_image_url_is_not_counted_as_link(self) -> None:
        original = "![img](https://example.com/i.png)"
        revised = "![img](https://example.com/i.png)"

        assert invariants.check_invariants(original, revised) == []


class TestHeadingDrift:
    def test_removed_heading_is_reported(self) -> None:
        revised = ORIGINAL.replace("### Detail\n\n", "")

        warnings = invariants.check_invariants(ORIGINAL, revised)

        assert any("Heading structure changed" in w for w in warnings)

    def test_demoted_heading_is_reported(self) -> None:
        revised = ORIGINAL.replace("## Step 2", "### Step 2")

        warnings = invariants.check_invariants(ORIGINAL, revised)

        assert any("Heading structure changed" in w for w in warnings)

    def test_reworded_heading_is_not_reported(self) -> None:
        revised = ORIGINAL.replace("## Step 1", "## First step")

        assert invariants.check_invariants(ORIGINAL, revised) == []
