"""Tests for splitting a prototype guide's Markdown into its sections."""

from app.guidance.prototype import sections

_GUIDE = """# Final Payment Case Check Guide

An introduction before the first section.

## 1 Background

Why the check exists.

## 2 Checking process

How to check.

### 2.1 Allocating a Quality Check

Who gets the check.

## 3 Rework

What to do next.

## Annex A – Case Types

The case types.
"""


class TestSplit:
    def test_finds_every_section_heading_in_order(self) -> None:
        found = sections.split(_GUIDE)

        assert [(s.number, s.heading) for s in found] == [
            ("1", "Background"),
            ("2", "Checking process"),
            ("2.1", "Allocating a Quality Check"),
            ("3", "Rework"),
            ("annex-a-case-types", "Annex A – Case Types"),
        ]

    def test_levels_a_section_one_below_its_hashes(self) -> None:
        found = sections.split(_GUIDE)

        assert [s.level for s in found] == [1, 1, 2, 1, 1]

    def test_a_section_runs_to_the_next_heading_of_any_level(
        self,
    ) -> None:
        found = {s.number: s for s in sections.split(_GUIDE)}

        assert found["2"].markdown == "## 2 Checking process\n\nHow to check.\n"

    def test_the_last_section_runs_to_the_end(self) -> None:
        found = sections.split(_GUIDE)

        assert found[-1].markdown == "## Annex A – Case Types\n\nThe case types.\n"

    def test_a_guide_without_section_headings_has_no_sections(self) -> None:
        assert sections.split("# A title\n\nJust text.\n") == []


class TestFind:
    def test_finds_a_section_by_its_number(self) -> None:
        found = sections.find(_GUIDE, "2.1")

        assert found is not None
        assert found.heading == "Allocating a Quality Check"

    def test_finds_an_unnumbered_section_by_its_slug(self) -> None:
        found = sections.find(_GUIDE, "annex-a-case-types")

        assert found is not None
        assert found.heading == "Annex A – Case Types"

    def test_finds_nothing_for_an_unknown_number(self) -> None:
        assert sections.find(_GUIDE, "9") is None
