"""Applying editor corrections to a parsed guidance section.

A section's stored Markdown is regenerated from the manifest rather than taken
verbatim from the client, so an edit can change a heading's text and a section's
body but can never move, renumber or re-level a section.

Three artefacts are kept in step, because different consumers read different
ones: the section file (read by the publishing checker), the manifest (the
frontend's table of contents) and ``content.md`` (read by the review checker).
"""

import json
import logging
import uuid

from app.guidance.documents import api_schemas, s3_repository, sectioning

logger = logging.getLogger(__name__)

# Only ASCII layout whitespace is trimmed. Notably this excludes U+00A0, which
# str.strip() would treat as whitespace: a non-breaking space is a character the
# author chose, and Word-derived guidance is full of them.
_TRIMMABLE_WHITESPACE = " \t\r\n"


class SectionNotFoundError(Exception):
    """Raised when the manifest has no section with the requested number."""


def compose_section_markdown(level: int, number: str, heading: str, body: str) -> str:
    """Render a section file exactly as the parse pipeline would.

    Mirrors ``pipeline.renderers.markdown.section_to_markdown``: the heading is
    one hash deeper than the section's level, because ``#`` is reserved for the
    document title, and the file ends in a single newline.

    Args:
        level: The section's depth from the manifest, 1 for a top-level section.
        number: The section's dotted number, e.g. ``1.2.3``.
        heading: The heading text, without its number.
        body: The section's Markdown content, without its heading line.

    Returns:
        The complete section file contents.
    """
    heading_line = f"{'#' * (level + 1)} {number} {heading}"
    normalised_body = (
        body.replace("\r\n", "\n").replace("\r", "\n").strip(_TRIMMABLE_WHITESPACE)
    )

    if not normalised_body:
        return f"{heading_line}\n"

    return f"{heading_line}\n\n{normalised_body}\n"


def _find_section(
    manifest: api_schemas.DocumentManifestResponse, section_number: str
) -> api_schemas.ManifestSectionNodeResponse:
    """Return the manifest node for a section number, or raise."""
    for node in manifest.sections:
        if node.number == section_number:
            return node

    msg = f"Section {section_number} not found in document {manifest.document_id}"
    raise SectionNotFoundError(msg)


async def _regenerate_content(
    s3_repo: s3_repository.AbstractGuidanceStorageRepository,
    document_id: uuid.UUID,
    manifest: api_schemas.DocumentManifestResponse,
) -> None:
    """Rebuild content.md from the stored section files.

    Byte-identical to ``pipeline.renderers.markdown.to_markdown``: the title as
    the sole ``#`` heading, then every section in document order.
    """
    numbers = [node.number for node in manifest.sections]
    joined = await sectioning.fetch_joined_sections(s3_repo, document_id, numbers)
    await s3_repo.upload_content(document_id, f"# {manifest.title}\n\n{joined}")


async def update_section(
    s3_repo: s3_repository.AbstractGuidanceStorageRepository,
    document_id: uuid.UUID,
    section_number: str,
    heading: str,
    body: str,
) -> None:
    """Replace one section's heading text and body, keeping artefacts in step.

    Args:
        s3_repo: Storage holding the document's parsed artefacts.
        document_id: The document being edited.
        section_number: The section's dotted number, as it appears in the manifest.
        heading: Replacement heading text, without its number.
        body: Replacement Markdown content, without its heading line.

    Raises:
        SectionNotFoundError: If the manifest has no such section.
    """
    raw_manifest = await s3_repo.download_manifest(document_id)
    manifest = api_schemas.DocumentManifestResponse.model_validate_json(raw_manifest)

    node = _find_section(manifest, section_number)
    node.heading = heading

    await s3_repo.upload_section(
        document_id,
        section_number,
        compose_section_markdown(node.level, node.number, heading, body),
    )
    await s3_repo.upload_manifest(document_id, json.dumps(manifest.model_dump()))
    await _regenerate_content(s3_repo, document_id, manifest)

    logger.info("Updated section %s of document %s", section_number, document_id)
