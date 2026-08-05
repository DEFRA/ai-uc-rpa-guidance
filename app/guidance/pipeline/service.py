import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import docx.document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.guidance.pipeline import models

logger = logging.getLogger(__name__)

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
        self._bookmark_to_section: dict[str, models.SectionNode] = {}

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
        self._bookmark_to_section = {}

        for element in doc.element.body:
            tag = element.tag

            if tag == qn("w:tbl"):
                self._flush_list_items()
                table_node = self._parse_table(Table(element, doc))
                if self._stack:
                    self._stack[-1].section.content.append(table_node)

            elif tag == qn("w:p"):
                self._process_paragraph(Paragraph(element, doc))

            # Attribute any bookmarks in this element to the section that now sits
            # on top of the stack — for a heading paragraph that is the heading just
            # pushed; otherwise the enclosing section. Done after dispatch so a
            # bookmark wrapping a heading maps to that heading, not its predecessor.
            self._capture_bookmarks(element)

        self._flush_list_items()
        self._assign_numbers(self._tree.children)
        self._resolve_hyperlink_anchors()

        return self._tree

    def _capture_bookmarks(self, element: Any) -> None:
        """Record each w:bookmarkStart name against the current section.

        Bookmarks are the *targets* of Word cross-references. Capturing
        name -> section lets _resolve_hyperlink_anchors rewrite links by bookmark
        identity rather than by guessing from heading text.
        """
        if not self._stack:
            return
        section = self._stack[-1].section
        for bookmark in element.iter(qn("w:bookmarkStart")):
            name = bookmark.get(qn("w:name"))
            if name:
                self._bookmark_to_section.setdefault(name, section)

    def _resolve_hyperlink_anchors(self) -> None:
        """Rewrite internal '#<bookmark>' hrefs to '#<section number>'.

        Resolvable anchors point at a captured bookmark; their href becomes the
        target section's number, which the viewer turns into a section link.
        Unresolvable anchors are left untouched and logged — an unresolved
        cross-reference is a data signal, not something to drop silently.
        """
        numbers = {
            name: section.number for name, section in self._bookmark_to_section.items()
        }
        for span in self._iter_spans():
            if not isinstance(span, models.InlineSpan):
                continue
            href = span.hyperlink
            if not href.startswith("#"):
                continue
            number = numbers.get(href[1:])
            if number:
                span.hyperlink = f"#{number}"
            else:
                logger.warning(
                    "Unresolved intra-document link anchor %r in document %r",
                    href,
                    self._tree.title,
                )

    def _iter_spans(self) -> Iterator[models.Span]:
        """Yield every span in the tree, in document order."""

        def walk(section: models.SectionNode) -> Iterator[models.Span]:
            for node in section.content:
                if isinstance(node, models.ParagraphNode):
                    yield from node.spans
                elif isinstance(node, models.ListNode):
                    for item in node.items:
                        yield from item.spans
            for child in section.children:
                yield from walk(child)

        for section in self._tree.children:
            yield from walk(section)

    def _process_paragraph(self, paragraph: Paragraph) -> None:
        style: str = paragraph.style.name if paragraph.style else ""
        text: str = paragraph.text.strip()

        # An image-only paragraph is a block figure. A paragraph that also has text
        # keeps its image inline as an ImageSpan via _parse_spans below — the image
        # must not swallow the surrounding words.
        if not text:
            image_node = self._extract_image(paragraph)
            if image_node:
                self._flush_list_items()
                if self._stack:
                    self._stack[-1].section.content.append(image_node)
            return

        if style.startswith("Heading"):
            self._process_heading(text, style)

        elif self._is_list_paragraph(paragraph) and self._stack:
            spans = self._parse_spans(paragraph)
            level = self._get_list_level(paragraph)
            self._pending_list_items.append(
                models.ListItemNode(spans=spans, level=level)
            )

        elif self._stack:
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

    def _parse_spans(self, paragraph: Paragraph) -> list[models.Span]:
        """Build inline spans from a paragraph, one span per w:hyperlink or bare w:r.

        A w:hyperlink is a single link unit even when Word has split its anchor text
        across several runs (revision/proofing boundaries). Dispatching on the inline
        children in document order — rather than flattening every descendant run —
        keeps each hyperlink whole, each plain run distinct, and each inline image
        (e.g. an icon) in place as an ImageSpan.
        """
        spans: list[models.Span] = []

        for child in paragraph._element:
            tag = child.tag
            span: models.Span | None
            if tag == qn("w:hyperlink"):
                span = self._hyperlink_span(child, paragraph)
            elif tag == qn("w:r"):
                span = self._run_span(child, paragraph)
            else:
                continue

            if span is not None:
                spans.append(span)

        return spans

    def _hyperlink_span(
        self, hyperlink_elem: Any, paragraph: Paragraph
    ) -> models.InlineSpan | None:
        """Merge a w:hyperlink's runs into one span carrying the resolved target once.

        Formatting is taken from the first run — a hyperlink's anchor text is uniformly
        styled in practice, so this yields a single clean link rather than one fragment
        per run.
        """
        runs = list(hyperlink_elem.iter(qn("w:r")))
        text = "".join(
            node.text or "" for run in runs for node in run.findall(qn("w:t"))
        )
        if not text:
            return None

        rpr = runs[0].find(qn("w:rPr")) if runs else None
        bold, italic, underline, color = self._extract_run_formatting(rpr)
        return models.InlineSpan(
            text=text,
            bold=bold,
            italic=italic,
            underline=underline,
            hyperlink=self._hyperlink_target(hyperlink_elem, paragraph),
            color=color,
        )

    def _run_span(self, run_elem: Any, paragraph: Paragraph) -> models.Span | None:
        """Build a span from a single bare run.

        A run with text yields a formatted InlineSpan; a text-less run holding an
        inline image (icon) yields an ImageSpan; an empty run yields None.
        """
        text = "".join(node.text or "" for node in run_elem.findall(qn("w:t")))
        if text:
            rpr = run_elem.find(qn("w:rPr"))
            bold, italic, underline, color = self._extract_run_formatting(rpr)
            return models.InlineSpan(
                text=text,
                bold=bold,
                italic=italic,
                underline=underline,
                color=color,
            )

        found = self._run_image_blob(run_elem, paragraph.part.rels)
        if found is not None:
            image_blob, ext = found
            return models.ImageSpan(data=image_blob, ext=ext)

        return None

    @staticmethod
    def _hyperlink_target(hyperlink_elem: Any, paragraph: Paragraph) -> str:
        """Return the URL (external r:id) or '#anchor' (internal) of a w:hyperlink."""
        r_id = hyperlink_elem.get(qn("r:id"))
        if r_id and r_id in paragraph.part.rels:
            return str(paragraph.part.rels[r_id].target_ref)

        anchor = hyperlink_elem.get(qn("w:anchor"))
        return f"#{anchor}" if anchor else ""

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
        all_cells = [cell for row in table.rows for cell in row.cells]
        # A fully-merged table has every grid position pointing to the same TC element.
        # Normalise it to a single-header node so the renderer can treat it as a callout.
        if all_cells and len({cell._tc for cell in all_cells}) == 1:
            return models.TableNode(headers=[all_cells[0].text.strip()], rows=[])

        rows_data: list[list[str]] = []
        for row in table.rows:
            rows_data.append([cell.text.strip() for cell in row.cells])

        if rows_data:
            headers, body = rows_data[0], rows_data[1:]
        else:
            headers, body = [], []

        return models.TableNode(headers=headers, rows=body)

    _BLIP_PATH = (
        f".//{qn('wp:inline')}/{qn('a:graphic')}/{qn('a:graphicData')}"
        f"/{qn('pic:pic')}/{qn('pic:blipFill')}/{qn('a:blip')}"
    )

    @staticmethod
    def _run_image_blob(run_elem: Any, rels: Any) -> tuple[bytes, str] | None:
        """Return (blob, ext) for the first inline image in a run, or None.

        The single place that turns a pic:blip + relationship into image bytes,
        shared by the block-figure path (_extract_image) and inline runs (_run_span).
        """
        for blip in run_elem.findall(DocxParser._BLIP_PATH):
            embed_id = blip.get(qn("r:embed"))
            if embed_id is None:
                continue
            rel = rels.get(embed_id)
            if rel is None:
                continue
            ext = Path(rel.target_part.partname).suffix or ".png"
            return rel.target_part.blob, ext
        return None

    def _extract_image(self, paragraph: Paragraph) -> models.ImageNode | None:
        for run in paragraph.runs:
            found = self._run_image_blob(run.element, paragraph.part.rels)
            if found is None:
                continue

            image_blob, ext = found
            self._image_counter += 1
            return models.ImageNode(
                rel_path="",
                alt_text=paragraph.text.strip() or f"img_{self._image_counter}{ext}",
                data=image_blob,
                ext=ext,
            )

        return None

    # Core-properties titles that indicate an unfilled template rather than a real document title.
    _TEMPLATE_TITLES: frozenset[str] = frozenset({"Design Team Guidance Template"})

    # Styles that open the document's navigation, so the cover page has ended.
    _CONTENTS_STYLE_PREFIXES: tuple[str, ...] = ("toc", "contents", "table of contents")

    # Between the author's distinct title parts, and within one hand-wrapped part.
    _TITLE_SEPARATOR = " — "
    _WRAPPED_LINE_SEPARATOR = " "

    @staticmethod
    def _opens_contents(para: Paragraph) -> bool:
        """Whether this paragraph begins a table of contents."""
        name = para.style.name if para.style and para.style.name else ""
        return name.lower().startswith(DocxParser._CONTENTS_STYLE_PREFIXES)

    @staticmethod
    def _breaks_page(para: Paragraph) -> bool:
        """Whether this paragraph carries an explicit page break."""
        return any(
            br.get(qn("w:type")) == "page"
            for run in para.runs
            for br in run._r.findall(qn("w:br"))
        )

    @staticmethod
    def _cover_groups(doc: docx.document.Document) -> list[list[Paragraph]]:
        """Return the cover page's paragraphs, grouped as the author laid them out.

        The cover ends at the first page break or table of contents, whichever
        comes first. Within it a blank paragraph separates one part of the title
        from the next; consecutive paragraphs are one part that the author wrapped
        by hand because it was too long for the line. Runs of blanks — including
        the ones padding the top of the page — separate but never form a part of
        their own. Headers and footers are in a separate XML part and do not
        appear in doc.paragraphs, so they are excluded automatically.
        """
        groups: list[list[Paragraph]] = []
        part: list[Paragraph] = []
        for para in doc.paragraphs:
            if DocxParser._opens_contents(para) or DocxParser._breaks_page(para):
                break
            if para.text.strip():
                part.append(para)
            elif part:
                groups.append(part)
                part = []
        if part:
            groups.append(part)
        return groups

    @staticmethod
    def _extract_title(doc: docx.document.Document) -> str:
        """Return the document's title, preferring the one printed on the page.

        Where the cover marks its title with the Title style, only those
        paragraphs are taken; otherwise the whole cover is. Each part is put back
        together as the author wrapped it, and the parts joined with a dash. The
        text is joined but not otherwise tidied — defects in a title are findings
        for the QA checks to report, not noise to normalise away.
        """
        title = doc.core_properties.title
        if (
            title
            and isinstance(title, str)
            and title.strip()
            and title.strip() not in DocxParser._TEMPLATE_TITLES
        ):
            return title.strip()

        groups = DocxParser._cover_groups(doc)
        marked = [
            [p for p in group if p.style and p.style.name == "Title"]
            for group in groups
        ]
        if any(marked):
            groups = [group for group in marked if group]

        return DocxParser._TITLE_SEPARATOR.join(
            DocxParser._WRAPPED_LINE_SEPARATOR.join(para.text.strip() for para in group)
            for group in groups
        )

    @staticmethod
    def _assign_numbers(sections: list[models.SectionNode], prefix: str = "") -> None:
        for idx, section in enumerate(sections, start=1):
            section.number = f"{prefix}{idx}" if prefix else str(idx)
            DocxParser._assign_numbers(section.children, prefix=f"{section.number}.")

    @staticmethod
    def _is_list_paragraph(paragraph: Paragraph) -> bool:
        # Direct numbering on the paragraph decides it, overriding the list style:
        # numId=0 is the OOXML sentinel for "numbering removed" (no bullet), which is
        # how Word un-bullets a lead-in line that still carries a List Bullet style.
        num_pr = paragraph._element.find(f".//{qn('w:numPr')}")
        if num_pr is not None:
            num_id = num_pr.find(qn("w:numId"))
            numbering_removed = num_id is not None and num_id.get(qn(_W_VAL)) == "0"
            return not numbering_removed

        style_name = paragraph.style.name if paragraph.style else ""
        return style_name in _LIST_STYLES

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
