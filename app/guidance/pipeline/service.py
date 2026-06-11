from pathlib import Path
from typing import Any

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


def _is_prop_on(elem: Any) -> bool:
    """Return True when a boolean toggle element is present and not explicitly disabled.

    OOXML boolean toggle properties (w:b, w:i, etc.) are 'on' by default when
    the element exists. They can be turned off with w:val="false" or w:val="0".
    """
    if elem is None:
        return False
    val = elem.get(qn("w:val"))
    return val not in ("false", "0")


def _parse_spans(paragraph: Paragraph) -> list[models.InlineSpan]:
    """Extract inline spans with bold/italic/underline/hyperlink from a paragraph."""
    spans: list[models.InlineSpan] = []

    # Build a map of hyperlink element -> URL
    hyperlink_map: dict[int, str] = {}
    for hyperlink_elem in paragraph._element.findall(qn("w:hyperlink")):
        r_id = hyperlink_elem.get(qn("r:id"))
        url = ""
        if r_id and r_id in paragraph.part.rels:
            url = paragraph.part.rels[r_id].target_ref
        # Map each run element inside this hyperlink to the URL
        for run_elem in hyperlink_elem.findall(qn("w:r")):
            hyperlink_map[id(run_elem)] = url

    # Iterate all run elements (including those inside hyperlinks)
    for run_elem in paragraph._element.iter(qn("w:r")):
        text = "".join(node.text or "" for node in run_elem.findall(qn("w:t")))
        if not text:
            continue

        # Check formatting — use _is_prop_on so that w:val="false" / w:val="0"
        # (which explicitly disables an inherited property) is treated as off.
        rpr = run_elem.find(qn("w:rPr"))
        bold = False
        italic = False
        underline = False
        color = ""
        if rpr is not None:
            bold = _is_prop_on(rpr.find(qn("w:b")))
            italic = _is_prop_on(rpr.find(qn("w:i")))
            u_elem = rpr.find(qn("w:u"))
            underline = u_elem is not None and u_elem.get(qn("w:val"), "none") != "none"
            color_elem = rpr.find(qn("w:color"))
            if color_elem is not None:
                color = color_elem.get(qn("w:val"), "")

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


def _get_list_level(paragraph: Paragraph) -> int:
    """Determine list nesting level from paragraph style or numPr."""
    style_name = paragraph.style.name if paragraph.style else ""
    if "2" in style_name or "3" in style_name:
        return 1
    # Check numPr indent level
    num_pr = paragraph._element.find(f".//{qn('w:numPr')}")
    if num_pr is not None:
        ilvl = num_pr.find(qn("w:ilvl"))
        if ilvl is not None:
            return int(ilvl.get(qn("w:val"), "0"))
    return 0


def _is_list_paragraph(paragraph: Paragraph) -> bool:
    """Check if a paragraph is a list item."""
    style_name = paragraph.style.name if paragraph.style else ""
    if style_name in _LIST_STYLES:
        return True
    # Check for numPr (numbered list marker)
    return paragraph._element.find(f".//{qn('w:numPr')}") is not None


def _extract_image(
    paragraph: Paragraph,
    _doc: docx.document.Document,
    image_counter: list[int],
) -> models.ImageNode | None:
    """Extract the first inline image from a paragraph, return an ImageNode with bytes."""
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
            image_counter[0] += 1
            image_blob = rel.target_part.blob
            ext = Path(rel.target_part.partname).suffix or ".png"
            return models.ImageNode(
                rel_path="",
                alt_text=paragraph.text.strip() or f"img_{image_counter[0]}{ext}",
                data=image_blob,
                ext=ext,
            )
    return None


def _parse_table(table: Table) -> models.TableNode:
    """Convert a docx Table to a TableNode."""
    rows_data: list[list[str]] = []
    for row in table.rows:
        rows_data.append([cell.text.strip() for cell in row.cells])

    if rows_data:
        headers = rows_data[0]
        body = rows_data[1:]
    else:
        headers = []
        body = []

    return models.TableNode(headers=headers, rows=body)


def _assign_numbers(sections: list[models.SectionNode], prefix: str = "") -> None:
    """Recursively assign hierarchical section numbers."""
    for idx, section in enumerate(sections, start=1):
        section.number = f"{prefix}{idx}" if prefix else str(idx)
        _assign_numbers(section.children, prefix=f"{section.number}.")


def _flush_list_items(
    list_items: list[models.ListItemNode],
    stack: list[tuple[int, models.SectionNode]],
) -> None:
    """Flush accumulated list items into a ListNode on the current section."""
    if not list_items:
        return
    list_node = models.ListNode(items=list(list_items), list_type="bullet")
    if stack:
        stack[-1][1].content.append(list_node)
    list_items.clear()


def _process_paragraph(
    paragraph: Paragraph,
    doc: docx.document.Document,
    tree: models.DocumentTree,
    stack: list[tuple[int, models.SectionNode]],
    image_counter: list[int],
    pending_list_items: list[models.ListItemNode],
) -> None:
    """Process a paragraph element and update state."""
    style: str = paragraph.style.name if paragraph.style else ""
    text: str = paragraph.text.strip()

    image_node = _extract_image(paragraph, doc, image_counter)
    if image_node:
        _flush_list_items(pending_list_items, stack)
        if stack:
            stack[-1][1].content.append(image_node)
        return

    if style.startswith("Heading"):
        _flush_list_items(pending_list_items, stack)
        level = int(style.split()[-1])
        new_section = models.SectionNode(heading=text, level=level)

        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            stack[-1][1].children.append(new_section)
        else:
            tree.children.append(new_section)

        stack.append((level, new_section))

    elif _is_list_paragraph(paragraph) and text and stack:
        spans = _parse_spans(paragraph)
        level = _get_list_level(paragraph)
        pending_list_items.append(models.ListItemNode(spans=spans, level=level))

    elif text and stack:
        _flush_list_items(pending_list_items, stack)
        spans = _parse_spans(paragraph)
        stack[-1][1].content.append(models.ParagraphNode(spans=spans))


def _extract_title(doc: docx.document.Document) -> str:
    """Infer a document title from metadata, 'Title' style, or first Heading 1."""
    title = doc.core_properties.title
    if title and isinstance(title, str) and title.strip():
        return title.strip()
    for para in doc.paragraphs:
        if para.style and para.style.name == "Title" and para.text.strip():
            return para.text.strip()
    for para in doc.paragraphs:
        if para.style and para.style.name == "Heading 1" and para.text.strip():
            return para.text.strip()
    return ""


def parse_doc(
    doc: docx.document.Document,
    title: str | None = None,
) -> models.DocumentTree:
    """Parse a docx Document into a DocumentTree with nested sections.

    Images are held in memory on ImageNode.data; no disk writes are performed.
    """
    tree = models.DocumentTree(
        title=title if title is not None else _extract_title(doc)
    )
    stack: list[tuple[int, models.SectionNode]] = []
    image_counter = [0]
    pending_list_items: list[models.ListItemNode] = []

    for element in doc.element.body:
        tag = element.tag

        if tag == qn("w:tbl"):
            _flush_list_items(pending_list_items, stack)
            table = Table(element, doc)
            table_node = _parse_table(table)
            if stack:
                stack[-1][1].content.append(table_node)
        elif tag == qn("w:p"):
            paragraph = Paragraph(element, doc)
            _process_paragraph(
                paragraph,
                doc,
                tree,
                stack,
                image_counter,
                pending_list_items,
            )

    _flush_list_items(pending_list_items, stack)
    _assign_numbers(tree.children)

    return tree
