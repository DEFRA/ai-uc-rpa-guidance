"""Tests for the passage of a section that a search result shows."""

from app.guidance.search import excerpt

SECTION = (
    "### 3.1 Options that can occupy the same land\n"
    "\n"
    "Are you investigating SW6 and AB14?\n"
    "\n"
    "Check if the relevant Option can occupy the same land, using the "
    "[Option Compatibility Tables](https://example.org/tables.docx)\n"
    "\n"
    "- If 'Yes', check CRM for an option map.\n"
    "  - If the maps confirm the areas don't overlap, close the case.\n"
    "\n"
    "Continue to section 4.\n"
)


class TestCut:
    def test_is_the_guide_s_own_text_with_its_links(self) -> None:
        passage = excerpt.cut(SECTION, "Check if the relevant", "tables.docx)")

        assert passage == (
            "Check if the relevant Option can occupy the same land, using the "
            "[Option Compatibility Tables](https://example.org/tables.docx)"
        )

    def test_widens_to_the_whole_lines_it_starts_and_ends_in(self) -> None:
        passage = excerpt.cut(SECTION, "relevant Option", "same land")

        assert passage is not None
        assert passage.startswith("Check if the relevant Option")
        assert passage.endswith("(https://example.org/tables.docx)")

    def test_runs_across_lines_from_the_start_to_the_end(self) -> None:
        passage = excerpt.cut(SECTION, "Are you investigating", "an option map.")

        assert passage is not None
        assert passage.startswith("Are you investigating SW6 and AB14?")
        assert passage.endswith("- If 'Yes', check CRM for an option map.")

    def test_takes_the_indent_off_a_nested_list_item(self) -> None:
        passage = excerpt.cut(SECTION, "If the maps confirm", "close the case.")

        assert (
            passage == "- If the maps confirm the areas don't overlap, close the case."
        )

    def test_is_none_when_the_start_is_not_in_the_section(self) -> None:
        assert excerpt.cut(SECTION, "Words the guide never uses", "case.") is None

    def test_is_none_when_the_end_is_not_after_the_start(self) -> None:
        assert excerpt.cut(SECTION, "Continue to section", "Are you") is None


class TestLinkAddresses:
    def test_are_the_addresses_of_the_links_in_order(self) -> None:
        markdown = "See [the tables](https://a.example/t) and [section 7](#7-contact)."

        assert excerpt.link_addresses(markdown) == ["https://a.example/t", "#7-contact"]

    def test_are_none_in_text_without_links(self) -> None:
        assert excerpt.link_addresses("Check CRM (the activities tab).") == []
