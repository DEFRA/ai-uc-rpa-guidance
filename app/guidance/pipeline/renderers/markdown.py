import html
from functools import singledispatch

from app.guidance.pipeline import colours, models

# What a newline becomes inside a cell, a pipe row being one line and unable to hold
# another.
_CELL_BREAK = "<br>"

# A pipe ends a cell wherever it appears, so a pipe the document means as text has to
# say so. This is not the escaping feature: an unescaped pipe adds a column, where an
# unescaped asterisk only reads oddly.
_ESCAPED_PIPE = r"\|"


def _render_spans(spans: list[models.Span]) -> str:
    """Render inline spans using HTML tags for unambiguous nested formatting."""
    parts: list[str] = []
    for span in spans:
        if isinstance(span, models.ImageSpan):
            parts.append(f"![{span.alt_text}]({span.rel_path})")
            continue
        # Quotes are left alone: this is text, not an attribute, and escaping an
        # apostrophe to `&#x27;` puts a numeric entity in front of the reader --
        # Markdown renderers pass one through, and the editor shows it literally.
        text = html.escape(span.text, quote=False)
        if span.hyperlink:
            href = html.escape(span.hyperlink, quote=True)
            link_text = text.replace("]", "\\]")
            text = f"[{link_text}](<{href}>)"
        if span.underline and not span.hyperlink:
            text = f"<u>{text}</u>"
        if span.italic:
            text = f"<em>{text}</em>"
        if span.bold:
            text = f"<strong>{text}</strong>"
        # Outermost, so that a coloured run stays one span whatever marks it carries;
        # the editor re-tokenises what is inside the brackets as Markdown.
        colour = colours.name_for(span.color)
        if colour:
            text = colours.marked_up(text, colour)
        parts.append(text)
    return "".join(parts)


def _render_cell(cell: models.CellNode) -> str:
    """One cell as the single line a pipe row can hold.

    A cell's blocks are joined with `<br>` because the format has nothing else to
    offer: a row cannot contain a newline. The editor reads that form back as the
    blocks it stands for, so nothing is lost by writing it.
    """
    blocks = [_render_spans(p.spans) for p in cell.paragraphs]
    return _CELL_BREAK.join(blocks).replace("|", _ESCAPED_PIPE)


def _render_quote(paragraphs: list[models.ParagraphNode]) -> list[str]:
    """Blocks as one blockquote, every line of them marked.

    Marking only the first line is what breaks a box: Markdown reads the lines that
    follow as a lazy continuation of the same paragraph, so a callout saying three
    things arrives as one run-on sentence, and anything after a blank line falls out
    of the quote altogether.
    """
    lines: list[str] = []
    for paragraph in paragraphs:
        if lines:
            lines.append(">")
        lines.extend(f"> {line}" for line in _render_spans(paragraph.spans).split("\n"))

    return lines


def _render_table(table: models.TableNode) -> list[str]:
    if table.header is None or not table.header.cells:
        return []

    columns = len(table.header.cells)
    lines = [
        _render_row([_render_cell(cell) for cell in table.header.cells]),
        "| " + " | ".join("---" for _ in range(columns)) + " |",
    ]

    for row in table.rows:
        cells = [_render_cell(cell) for cell in row.cells][:columns]
        cells += [""] * (columns - len(cells))
        lines.append(_render_row(cells))

    lines.append("")
    return lines


def _render_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _render_list(list_node: models.ListNode) -> list[str]:
    lines: list[str] = []
    for idx, item in enumerate(list_node.items, start=1):
        indent = "  " * item.level
        text = _render_spans(item.spans)
        if list_node.list_type == "ordered":
            lines.append(f"{indent}{idx}. {text}")
        else:
            lines.append(f"{indent}- {text}")
    lines.append("")
    return lines


@singledispatch
def _render_content(node: models.ContentNode) -> list[str]:  # noqa: ARG001 - singledispatch fallback for future node types
    return []


@_render_content.register
def _(node: models.ParagraphNode) -> list[str]:
    return [_render_spans(node.spans), ""]


@_render_content.register
def _(node: models.TableNode) -> list[str]:
    return _render_table(node)


@_render_content.register
def _(node: models.CalloutNode) -> list[str]:
    return [*_render_quote(node.paragraphs), ""]


@_render_content.register
def _(node: models.ListNode) -> list[str]:
    return _render_list(node)


@_render_content.register
def _(node: models.ImageNode) -> list[str]:
    return [f"![{node.alt_text}]({node.rel_path})", ""]


def _render_section(section: models.SectionNode) -> list[str]:
    lines: list[str] = []

    prefix = "#" * (section.level + 1)
    lines.append(f"{prefix} {section.number} {section.heading}")
    lines.append("")

    for node in section.content:
        lines.extend(_render_content(node))

    for child in section.children:
        lines.extend(_render_section(child))

    return lines


def to_markdown(doc: models.DocumentTree) -> str:
    """Render a DocumentTree as Markdown."""
    lines: list[str] = [f"# {doc.title}", ""]

    for section in doc.children:
        lines.extend(_render_section(section))

    return "\n".join(lines)


def section_to_markdown(section: models.SectionNode) -> str:
    """Render a section's heading and direct content only, without recursing into children."""
    lines: list[str] = []
    prefix = "#" * (section.level + 1)
    lines.append(f"{prefix} {section.number} {section.heading}")
    lines.append("")
    for node in section.content:
        lines.extend(_render_content(node))
    return "\n".join(lines)
