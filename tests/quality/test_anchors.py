"""Tests for cutting a prototype guide into checkable parts and anchoring quotes."""

from app.quality import anchors

GUIDE = """# Land use guide

Some preamble.

## 1 Introduction

Read this first.

## 2 Assign case

Assign the case to yourself.

### 2.1 Force Majeure / Obvious Error review

Complete a review of CRM to confirm is any correspondence has been received.

#### 2.1.1 Deeper

Deeper text.

## Annex A – Case Types

The case types.
"""


class TestTopLevelParts:
    def test_one_part_per_top_level_section(self) -> None:
        parts = anchors.top_level_parts(GUIDE)

        assert [part.label for part in parts] == ["1", "2", "Annex A"]

    def test_a_part_holds_its_subsections(self) -> None:
        part = anchors.top_level_parts(GUIDE)[1]

        assert part.markdown == (
            "## 2 Assign case\n\nAssign the case to yourself.\n"
            "\n"
            "### 2.1 Force Majeure / Obvious Error review\n\n"
            "Complete a review of CRM to confirm is any correspondence has been received.\n"
            "\n"
            "#### 2.1.1 Deeper\n\nDeeper text.\n"
        )

    def test_title_and_preamble_are_not_checked(self) -> None:
        parts = anchors.top_level_parts(GUIDE)

        assert all("preamble" not in part.markdown for part in parts)

    def test_a_guide_with_no_sections_has_no_parts(self) -> None:
        assert anchors.top_level_parts("# Title only\n\nText.\n") == []

    def test_a_guide_whose_top_level_is_three_hashes(self) -> None:
        guide = "# T\n\n### 1 One\n\nA.\n\n#### 1.1 Sub\n\nB.\n\n### 2 Two\n\nC.\n"

        parts = anchors.top_level_parts(guide)

        assert [part.label for part in parts] == ["1", "2"]


class TestSectionLabel:
    def test_a_numbered_section_is_labelled_by_its_number(self) -> None:
        assert anchors.section_label("2.1", "Force Majeure") == "2.1"

    def test_an_annex_is_labelled_by_its_letter(self) -> None:
        label = anchors.section_label("annex-a-case-types", "Annex A – Case Types")

        assert label == "Annex A"

    def test_an_appendix_is_labelled_by_its_letter(self) -> None:
        label = anchors.section_label("appendix-b-forms", "Appendix B: Forms")

        assert label == "Appendix B"

    def test_another_unnumbered_section_is_labelled_by_its_heading(self) -> None:
        label = anchors.section_label("useful-contacts", "Useful contacts")

        assert label == "Useful contacts"


class TestAnchor:
    def test_prefix_and_suffix_surround_the_quote(self) -> None:
        part = anchors.top_level_parts(GUIDE)[1]

        anchor = anchors.anchor(part, "confirm is any correspondence")

        assert anchor is not None
        assert anchor.exact == "confirm is any correspondence"
        assert anchor.prefix == "\n\nComplete a review of CRM to "
        assert anchor.suffix == " has been received.\n\n#### 2.1."

    def test_prefix_and_suffix_stop_at_the_ends_of_the_part(self) -> None:
        part = anchors.top_level_parts(GUIDE)[0]

        anchor = anchors.anchor(part, "## 1 Introduction")

        assert anchor is not None
        assert anchor.prefix == ""

    def test_a_quote_not_in_the_part_has_no_anchor(self) -> None:
        part = anchors.top_level_parts(GUIDE)[1]

        assert anchors.anchor(part, "not in the guide") is None

    def test_a_quote_that_appears_twice_has_no_anchor(self) -> None:
        part = anchors.top_level_parts(GUIDE)[1]

        assert anchors.anchor(part, "case") is None

    def test_an_empty_quote_has_no_anchor(self) -> None:
        part = anchors.top_level_parts(GUIDE)[1]

        assert anchors.anchor(part, "") is None

    def test_the_anchor_names_the_subsection_that_holds_the_quote(self) -> None:
        part = anchors.top_level_parts(GUIDE)[1]

        anchor = anchors.anchor(part, "confirm is any correspondence")

        assert anchor is not None
        assert anchor.section == "2.1"

    def test_a_quote_in_a_deeper_subsection_is_labelled_by_that_subsection(
        self,
    ) -> None:
        part = anchors.top_level_parts(GUIDE)[1]

        anchor = anchors.anchor(part, "Deeper text.")

        assert anchor is not None
        assert anchor.section == "2.1.1"

    def test_a_quote_in_an_annex_is_labelled_by_the_annex(self) -> None:
        part = anchors.top_level_parts(GUIDE)[2]

        anchor = anchors.anchor(part, "The case types.")

        assert anchor is not None
        assert anchor.section == "Annex A"
