from pathlib import Path
from typing import Any, NamedTuple

import docx.document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.guidance.pipeline import models

_LIST_STYLES = frozenset(
    {
        "List Bullet",
        "List Bullet 2",
        "List Bullet 3",
        "List Paragraph",
        "Table bullet 1",
    }
)

_W_VAL = "w:val"


class StackEntry(NamedTuple):
    level: int
    section: models.SectionNode


class DocxParser:
    """Stateful parser that transforms a python-docx Document into a DocumentTree.

    A fresh instance must be created for each document (state is not reusable).
    """

    def __init__(self) -> None:
        self._tree: models.DocumentTree = models.DocumentTree(title="")
        self._stack: list[StackEntry] = []
        self._image_counter: int = 0
        self._pending_list_items: list[models.ListItemNode] = []

    def parse(
        self,
        doc: docx.document.Document,
        title: str | None = None,
    ) -> models.DocumentTree:
        """Parse a docx Document into a DocumentTree with nested sections.

        Images are held in memory on ImageNode.data; no disk writes are performed.
        """
        self._tree = models.DocumentTree(
            title=title if title is not None else self._extract_title(doc)
        )
        self._stack = []
        self._image_counter = 0
        self._pending_list_items = []

        for element in doc.element.body:
            tag = element.tag

            if tag == qn("w:tbl"):
                self._flush_list_items()
                table_node = self._parse_table(Table(element, doc))
                if self._stack:
                    self._stack[-1].section.content.append(table_node)

            elif tag == qn("w:p"):
                self._process_paragraph(Paragraph(element, doc))

        self._flush_list_items()
        self._assign_numbers(self._tree.children)

        return self._tree

    def _process_paragraph(self, paragraph: Paragraph) -> None:
        style: str = paragraph.style.name if paragraph.style else ""
        text: str = paragraph.text.strip()

        image_node = self._extract_image(paragraph)
        if image_node:
            self._flush_list_items()
            if self._stack:
                self._stack[-1].section.content.append(image_node)
            return

        if style.startswith("Heading"):
            self._process_heading(text, style)

        elif self._is_list_paragraph(paragraph) and text and self._stack:
            spans = self._parse_spans(paragraph)
            level = self._get_list_level(paragraph)
            self._pending_list_items.append(
                models.ListItemNode(spans=spans, level=level)
            )

        elif text and self._stack:
            self._flush_list_items()
            spans = self._parse_spans(paragraph)
            self._stack[-1].section.content.append(models.ParagraphNode(spans=spans))

    def _process_heading(self, text: str, style: str) -> None:
        self._flush_list_items()

        level = int(style.split()[-1])
        new_section = models.SectionNode(heading=text, level=level)

        while self._stack and self._stack[-1].level >= level:
            self._stack.pop()

        if self._stack:
            self._stack[-1].section.children.append(new_section)
        else:
            self._tree.children.append(new_section)

        self._stack.append(StackEntry(level, new_section))

    def _flush_list_items(self) -> None:
        if not self._pending_list_items:
            return

        list_node = models.ListNode(
            items=list(self._pending_list_items), list_type="bullet"
        )
        if self._stack:
            self._stack[-1].section.content.append(list_node)

        self._pending_list_items.clear()

    def _parse_spans(self, paragraph: Paragraph) -> list[models.InlineSpan]:
        hyperlink_map = self._build_hyperlink_map(paragraph)
        spans: list[models.InlineSpan] = []

        for run_elem in paragraph._element.iter(qn("w:r")):
            text = "".join(node.text or "" for node in run_elem.findall(qn("w:t")))
            if not text:
                continue

            rpr = run_elem.find(qn("w:rPr"))
            bold, italic, underline, color = self._extract_run_formatting(rpr)
            hyperlink = hyperlink_map.get(id(run_elem), "")

            spans.append(
                models.InlineSpan(
                    text=text,
                    bold=bold,
                    italic=italic,
                    underline=underline,
                    hyperlink=hyperlink,
                    color=color,
                )
            )

        return spans

    @staticmethod
    def _build_hyperlink_map(paragraph: Paragraph) -> dict[int, str]:
        hyperlink_map: dict[int, str] = {}

        for hyperlink_elem in paragraph._element.findall(qn("w:hyperlink")):
            r_id = hyperlink_elem.get(qn("r:id"))
            url = ""
            if r_id and r_id in paragraph.part.rels:
                url = paragraph.part.rels[r_id].target_ref

            for run_elem in hyperlink_elem.findall(qn("w:r")):
                hyperlink_map[id(run_elem)] = url

        return hyperlink_map

    @staticmethod
    def _extract_run_formatting(rpr: Any) -> tuple[bool, bool, bool, str]:
        if rpr is None:
            return False, False, False, ""

        bold = DocxParser._is_prop_on(rpr.find(qn("w:b")))
        italic = DocxParser._is_prop_on(rpr.find(qn("w:i")))

        u_elem = rpr.find(qn("w:u"))
        underline = u_elem is not None and u_elem.get(qn(_W_VAL), "none") != "none"

        color_elem = rpr.find(qn("w:color"))
        color = color_elem.get(qn(_W_VAL), "") if color_elem is not None else ""

        return bold, italic, underline, color

    @staticmethod
    def _is_prop_on(elem: Any) -> bool:
        """Return True when a boolean toggle element is present and not explicitly disabled.

        OOXML boolean toggle properties (w:b, w:i, etc.) are 'on' by default when
        the element exists. They can be turned off with w:val="false" or w:val="0".
        """
        if elem is None:
            return False
        val = elem.get(qn(_W_VAL))
        return val not in ("false", "0")

    @staticmethod
    def _parse_table(table: Table) -> models.TableNode:
        rows_data: list[list[str]] = []
        for row in table.rows:
            rows_data.append([cell.text.strip() for cell in row.cells])

        if rows_data:
            headers, body = rows_data[0], rows_data[1:]
        else:
            headers, body = [], []

        return models.TableNode(headers=headers, rows=body)

    def _extract_image(self, paragraph: Paragraph) -> models.ImageNode | None:
        for run in paragraph.runs:
            inline_shapes = run.element.findall(
                f".//{qn('wp:inline')}/{qn('a:graphic')}/{qn('a:graphicData')}"
                f"/{qn('pic:pic')}/{qn('pic:blipFill')}/{qn('a:blip')}",
            )
            for blip in inline_shapes:
                embed_id = blip.get(qn("r:embed"))
                if embed_id is None:
                    continue

                rel = paragraph.part.rels.get(embed_id)
                if rel is None:
                    continue

                self._image_counter += 1
                image_blob = rel.target_part.blob
                ext = Path(rel.target_part.partname).suffix or ".png"

                return models.ImageNode(
                    rel_path="",
                    alt_text=paragraph.text.strip()
                    or f"img_{self._image_counter}{ext}",
                    data=image_blob,
                    ext=ext,
                )

        return None

    # Core-properties titles that indicate an unfilled template rather than a real document title.
    _TEMPLATE_TITLES: frozenset[str] = frozenset({"Design Team Guidance Template"})

    @staticmethod
    def _first_page_lines(doc: docx.document.Document) -> list[str]:
        """Return text of all non-empty paragraphs before the first explicit page break.

        Headers and footers are in a separate XML part and do not appear in
        doc.paragraphs, so they are excluded automatically.
        """
        lines: list[str] = []
        for para in doc.paragraphs:
            if any(
                br.get(qn("w:type")) == "page"
                for run in para.runs
                for br in run._r.findall(qn("w:br"))
            ):
                break
            if para.text.strip():
                lines.append(para.text.strip())
        return lines

    @staticmethod
    def _extract_title(doc: docx.document.Document) -> str:
        title = doc.core_properties.title
        if (
            title
            and isinstance(title, str)
            and title.strip()
            and title.strip() not in DocxParser._TEMPLATE_TITLES
        ):
            return title.strip()

        for para in doc.paragraphs:
            if para.style and para.style.name == "Title" and para.text.strip():
                return para.text.strip()

        lines = DocxParser._first_page_lines(doc)
        if lines:
            return " — ".join(lines)

        return ""

    @staticmethod
    def _assign_numbers(sections: list[models.SectionNode], prefix: str = "") -> None:
        for idx, section in enumerate(sections, start=1):
            section.number = f"{prefix}{idx}" if prefix else str(idx)
            DocxParser._assign_numbers(section.children, prefix=f"{section.number}.")

    @staticmethod
    def _is_list_paragraph(paragraph: Paragraph) -> bool:
        style_name = paragraph.style.name if paragraph.style else ""
        if style_name in _LIST_STYLES:
            return True
        return paragraph._element.find(f".//{qn('w:numPr')}") is not None

    @staticmethod
    def _get_list_level(paragraph: Paragraph) -> int:
        style_name = paragraph.style.name if paragraph.style else ""
        if style_name.endswith((" 2", " 3")):
            return 1

        num_pr = paragraph._element.find(f".//{qn('w:numPr')}")
        if num_pr is not None:
            ilvl = num_pr.find(qn("w:ilvl"))
            if ilvl is not None:
                return int(ilvl.get(qn(_W_VAL), "0"))

        return 0


def parse_doc(
    doc: docx.document.Document,
    title: str | None = None,
) -> models.DocumentTree:
    """Convenience wrapper — delegates to DocxParser."""
    return DocxParser().parse(doc, title=title)
