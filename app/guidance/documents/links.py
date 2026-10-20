"""The guidance documents a document links to, found by the filename in a SharePoint link.

A guide refers to another guide by a SharePoint link, not by anything the API knows,
so the filename in the link is the one thing that can be matched with the filename an
uploaded document arrived with.
"""

import urllib.parse
from collections.abc import Iterator

from app.guidance.pipeline import models as pipeline_models

_SHAREPOINT_HOST = ".sharepoint.com"
_WORD_EXTENSION = ".docx"


def linked_filename(url: str) -> str | None:
    """Return the filename of the Word document a SharePoint link opens, or None.

    A link opens a document either by its path (``/:w:/r/…/<name>.docx``) or through
    ``Doc.aspx``, which names it in its ``file`` parameter.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.hostname or not parts.hostname.endswith(_SHAREPOINT_HOST):
        return None
    named_by_parameter = urllib.parse.parse_qs(parts.query).get("file", [])
    path_name = urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
    filename = named_by_parameter[0] if named_by_parameter else path_name
    return filename if filename.lower().endswith(_WORD_EXTENSION) else None


def linked_filenames(tree: pipeline_models.DocumentTree) -> list[str]:
    """Return the filenames of every Word document the tree links to, sorted, once each."""
    filenames = {
        linked_filename(span.hyperlink)
        for section in tree.sections
        for node in section.content
        for span in _spans(node)
        if isinstance(span, pipeline_models.InlineSpan) and span.hyperlink
    }
    return sorted(filename for filename in filenames if filename)


def _spans(node: pipeline_models.ContentNode) -> Iterator[pipeline_models.Span]:
    """Yield every span a content node holds, tables and callouts included."""
    if isinstance(node, pipeline_models.ParagraphNode):
        yield from node.spans
    if isinstance(node, pipeline_models.ListNode):
        for item in node.items:
            yield from item.spans
    if isinstance(node, pipeline_models.CalloutNode):
        for paragraph in node.paragraphs:
            yield from paragraph.spans
    if isinstance(node, pipeline_models.TableNode):
        rows = [node.header, *node.rows] if node.header else node.rows
        for row in rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    yield from paragraph.spans
