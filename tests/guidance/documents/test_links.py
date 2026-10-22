"""Tests for finding the guidance documents a document links to."""

from app.guidance.documents import links
from app.guidance.pipeline import models as pipeline_models

_PATH_LINK = (
    "https://defra.sharepoint.com/:w:/r/teams/Team3528/Content%20Cloud1/"
    "Domestic%20Revenue%20Claims/2026/Final%20Payment/"
    "2026%20CS%20MA%20Claim%20%20Admin%20Evidence%20Check.docx"
    "?d=w4553979a28594415b32e7154742b99a6&csf=1&web=1&e=4o2E9j"
)
_DOC_ASPX_LINK = (
    "https://defra.sharepoint.com/:w:/r/teams/Team3528/_layouts/15/Doc.aspx"
    "?sourcedoc=%7BDA9299CB-08E0-4481-8BF5-485C65AE34CF%7D"
    "&file=Locate%20the%20Latest%20Version%20of%20the%20Agreement.docx"
    "&action=default&mobileredirect=true&DefaultItemOpen=1"
)
_FOLDER_LINK = (
    "https://defra.sharepoint.com/teams/Team3528/Content%20Cloud1/Forms/"
    "countrysidestewardship.aspx?id=%2Fteams%2FTeam3528%2FContent%20Cloud1%2FGeneral"
)


def _link(url: str) -> pipeline_models.InlineSpan:
    return pipeline_models.InlineSpan(text="a link", hyperlink=url)


def _paragraph(url: str) -> pipeline_models.ParagraphNode:
    return pipeline_models.ParagraphNode(spans=[_link(url)])


def _tree(*content: pipeline_models.ContentNode) -> pipeline_models.DocumentTree:
    section = pipeline_models.SectionNode(
        heading="Section", level=1, number="1", content=list(content)
    )
    return pipeline_models.DocumentTree(title="Guide", children=[section])


class TestLinkedFilename:
    def test_takes_the_filename_from_the_path_of_a_sharepoint_link(self) -> None:
        assert (
            links.linked_filename(_PATH_LINK)
            == "2026 CS MA Claim  Admin Evidence Check.docx"
        )

    def test_takes_the_filename_from_the_file_parameter_of_a_doc_aspx_link(
        self,
    ) -> None:
        assert (
            links.linked_filename(_DOC_ASPX_LINK)
            == "Locate the Latest Version of the Agreement.docx"
        )

    def test_ignores_a_sharepoint_link_to_something_other_than_a_word_document(
        self,
    ) -> None:
        assert links.linked_filename(_FOLDER_LINK) is None

    def test_ignores_a_link_outside_sharepoint(self) -> None:
        assert links.linked_filename("https://www.gov.uk/guide.docx") is None

    def test_ignores_a_link_within_the_document(self) -> None:
        assert links.linked_filename("#2.1") is None


class TestLinkedFilenames:
    def test_finds_links_in_paragraphs_lists_tables_and_callouts(self) -> None:
        tree = _tree(
            _paragraph(_PATH_LINK),
            pipeline_models.ListNode(
                items=[pipeline_models.ListItemNode(spans=[_link(_DOC_ASPX_LINK)])]
            ),
            pipeline_models.TableNode(
                rows=[
                    pipeline_models.RowNode(
                        cells=[
                            pipeline_models.CellNode(
                                paragraphs=[
                                    _paragraph(
                                        "https://defra.sharepoint.com/:w:/r/a/In%20A%20Table.docx"
                                    )
                                ]
                            )
                        ]
                    )
                ]
            ),
            pipeline_models.CalloutNode(
                paragraphs=[
                    _paragraph("https://defra.sharepoint.com/:w:/r/a/In%20A%20Box.docx")
                ]
            ),
        )

        assert links.linked_filenames(tree) == [
            "2026 CS MA Claim  Admin Evidence Check.docx",
            "In A Box.docx",
            "In A Table.docx",
            "Locate the Latest Version of the Agreement.docx",
        ]

    def test_finds_links_in_a_table_header(self) -> None:
        header = pipeline_models.RowNode(
            cells=[pipeline_models.CellNode(paragraphs=[_paragraph(_PATH_LINK)])]
        )

        tree = _tree(pipeline_models.TableNode(header=header))

        assert links.linked_filenames(tree) == [
            "2026 CS MA Claim  Admin Evidence Check.docx"
        ]

    def test_lists_a_document_linked_more_than_once_only_once(self) -> None:
        tree = _tree(_paragraph(_PATH_LINK), _paragraph(_PATH_LINK))

        assert links.linked_filenames(tree) == [
            "2026 CS MA Claim  Admin Evidence Check.docx"
        ]

    def test_finds_links_in_subsections(self) -> None:
        subsection = pipeline_models.SectionNode(
            heading="Subsection",
            level=2,
            number="1.1",
            content=[_paragraph(_PATH_LINK)],
        )
        section = pipeline_models.SectionNode(
            heading="Section", level=1, number="1", children=[subsection]
        )

        tree = pipeline_models.DocumentTree(title="Guide", children=[section])

        assert links.linked_filenames(tree) == [
            "2026 CS MA Claim  Admin Evidence Check.docx"
        ]
