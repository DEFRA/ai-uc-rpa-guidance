import html

from app.guidance.pipeline import models


def _render_spans(spans: list[models.InlineSpan]) -> str:
    """Render inline spans using HTML tags for unambiguous nested formatting.

    HTML is used instead of Markdown delimiters because Word documents
    commonly produce multiple adjacent runs with the same formatting, which
    causes delimiter-collision issues (e.g. ``**word1****word2**``) in
    Markdown.  HTML tags nest cleanly regardless of adjacency or combination:

    * bold link  →  ``<strong><a href="url">text</a></strong>``
    * bold+italic →  ``<strong><em>text</em></strong>``
    * underline   →  ``<u>text</u>``

    Text content and URLs are HTML-escaped to prevent broken markup.
    """
    parts: list[str] = []
    for span in spans:
        text = html.escape(span.text)
        if span.hyperlink:
            href = html.escape(span.hyperlink, quote=True)
            text = f'<a href="{href}">{text}</a>'
        if span.underline:
            text = f"<u>{text}</u>"
        if span.italic:
            text = f"<em>{text}</em>"
        if span.bold:
            text = f"<strong>{text}</strong>"
        parts.append(text)
    return "".join(parts)


def _render_table(table: models.TableNode) -> list[str]:
    """Render a TableNode as a GFM pipe table."""
    if not table.headers:
        return []

    lines: list[str] = []
    header_line = "| " + " | ".join(table.headers) + " |"
    separator = "| " + " | ".join("---" for _ in table.headers) + " |"
    lines.append(header_line)
    lines.append(separator)

    for row in table.rows:
        # Pad row to match header count
        padded = row + [""] * (len(table.headers) - len(row))
        lines.append("| " + " | ".join(padded[: len(table.headers)]) + " |")

    lines.append("")
    return lines


def _render_list(list_node: models.ListNode) -> list[str]:
    """Render a ListNode as Markdown list items."""
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


def _render_section(section: models.SectionNode) -> list[str]:
    """Recursively render a section and its children."""
    lines: list[str] = []

    prefix = "#" * (section.level + 1)
    lines.append(f"{prefix} {section.number} {section.heading}")
    lines.append("")

    for node in section.content:
        if isinstance(node, models.ParagraphNode):
            lines.append(_render_spans(node.spans))
            lines.append("")
        elif isinstance(node, models.TableNode):
            lines.extend(_render_table(node))
        elif isinstance(node, models.ListNode):
            lines.extend(_render_list(node))
        elif isinstance(node, models.ImageNode):
            lines.append(f"![{node.alt_text}]({node.rel_path})")
            lines.append("")

    for child in section.children:
        lines.extend(_render_section(child))

    return lines


def to_markdown(doc: models.DocumentTree) -> str:
    """Render a DocumentTree as Markdown."""
    lines: list[str] = [f"# {doc.title}", ""]

    for section in doc.children:
        lines.extend(_render_section(section))

    return "\n".join(lines)
