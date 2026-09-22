"""Tests for the document's acronym reverse index."""

from app.guidance.summaries import models


def _used(acronym: str, expansion: str | None = None) -> models.SectionAcronym:
    return models.SectionAcronym(acronym=acronym, expansion=expansion)


class TestBuildAcronymIndex:
    def test_keys_the_index_by_acronym(self) -> None:
        index = models.build_acronym_index(
            [("1", [_used("SDA", "Severely Disadvantaged Area")])]
        )

        assert [entry.acronym for entry in index] == ["SDA"]
        assert index[0].expansion == "Severely Disadvantaged Area"
        assert index[0].sections == ["1"]

    def test_names_every_section_an_acronym_appears_in(self) -> None:
        index = models.build_acronym_index(
            [("1", [_used("SSSI")]), ("2", [_used("SSSI")]), ("2.1", [_used("SSSI")])]
        )

        assert index[0].sections == ["1", "2", "2.1"]

    def test_expands_an_acronym_the_document_expands_anywhere(self) -> None:
        index = models.build_acronym_index(
            [
                ("1", [_used("FER")]),
                ("2", [_used("FER", "Farm Environment Record")]),
                ("3", [_used("FER")]),
            ]
        )

        assert index[0].expansion == "Farm Environment Record"
        assert index[0].sections == ["1", "2", "3"]

    def test_records_an_acronym_the_document_never_expands(self) -> None:
        index = models.build_acronym_index([("1", [_used("IAPA")])])

        assert index[0].acronym == "IAPA"
        assert index[0].expansion is None

    def test_orders_the_index_alphabetically(self) -> None:
        index = models.build_acronym_index(
            [("1", [_used("SSSI"), _used("CRM"), _used("LFA")])]
        )

        assert [entry.acronym for entry in index] == ["CRM", "LFA", "SSSI"]

    def test_is_empty_for_a_document_whose_sections_use_none(self) -> None:
        assert models.build_acronym_index([("1", []), ("2", [])]) == []
