"""Tests for the index as the search agent is shown it."""

import uuid

from app.guidance.search import index_text
from app.guidance.summaries import models

DOCUMENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _summary(**overrides: object) -> models.DocumentSummary:
    defaults: dict[str, object] = {
        "document_id": DOCUMENT_ID,
        "title": "A Guide",
        "about": "What it is about.",
        "used_for": "What it is used for.",
        "keywords": ["ROCR"],
        "acronyms": [
            models.Acronym(
                acronym="SDA",
                expansion="Severely Disadvantaged Area",
                sections=["1"],
            )
        ],
        "path": "s3://bucket/summary.md",
        "start_path": f"/guidance-documents/{DOCUMENT_ID}/sections/1",
        "model": "anthropic.claude-sonnet-4-6",
    }
    defaults.update(overrides)
    return models.DocumentSummary(**defaults)  # type: ignore[arg-type]


def _section(number: str = "1") -> models.SectionSummary:
    return models.SectionSummary(
        document_id=DOCUMENT_ID,
        number=number,
        heading=f"Heading {number}",
        level=1,
        summary=f"What section {number} covers.",
        keywords=["a term"],
        acronyms=[models.SectionAcronym(acronym="IAPA", expansion=None)],
        start_path=f"/guidance-documents/{DOCUMENT_ID}/sections/{number}",
        order=0,
    )


class TestRender:
    def test_names_the_document_by_the_id_a_result_must_use(self) -> None:
        rendered = index_text.render([_summary()], {})

        assert f"document_id: {DOCUMENT_ID}" in rendered

    def test_gives_the_summary_the_terms_and_the_acronym_index(self) -> None:
        rendered = index_text.render([_summary()], {})

        assert "About: What it is about." in rendered
        assert "Used for: What it is used for." in rendered
        assert "Terms: ROCR" in rendered
        assert "SDA = Severely Disadvantaged Area [1]" in rendered

    def test_lists_each_section_with_the_number_a_result_must_use(self) -> None:
        rendered = index_text.render(
            [_summary()], {str(DOCUMENT_ID): [_section("1"), _section("2.1")]}
        )

        assert "section_number: 1 | Heading 1 | What section 1 covers." in rendered
        assert "section_number: 2.1 | Heading 2.1" in rendered

    def test_spells_out_a_sections_acronyms(self) -> None:
        rendered = index_text.render([_summary()], {str(DOCUMENT_ID): [_section()]})

        assert "Acronyms: IAPA = not expanded" in rendered

    def test_renders_a_document_whose_sections_are_not_indexed(self) -> None:
        rendered = index_text.render([_summary()], {})

        assert "Sections:" not in rendered

    def test_renders_an_empty_index_as_nothing(self) -> None:
        assert index_text.render([], {}) == ""
