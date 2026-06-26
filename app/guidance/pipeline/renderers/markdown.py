import html
from functools import singledispatch

from app.guidance.pipeline import models


def _render_spans(spans: list[models.InlineSpan]) -> str:
    """Render inline spans using HTML tags for unambiguous nested formatting."""
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
    if not table.headers:
        return []

    # 1×1 tables are used in Word as callout boxes — render as a blockquote
    if len(table.headers) == 1 and not table.rows:
        return [f"> {table.headers[0]}", ""]

    lines: list[str] = []
    header_line = "| " + " | ".join(table.headers) + " |"
    separator = "| " + " | ".join("---" for _ in table.headers) + " |"
    lines.append(header_line)
    lines.append(separator)

    for row in table.rows:
        padded = row + [""] * (len(table.headers) - len(row))
        lines.append("| " + " | ".join(padded[: len(table.headers)]) + " |")

    lines.append("")
    return lines


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
