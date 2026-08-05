"""Manifest-driven section topology and content assembly.

The manifest is always a parameter: callers download and parse it once, then
compose these functions, so repeated walks never re-fetch it.
"""

import uuid

from app.guidance.documents import api_schemas, s3_repository


def section_and_descendant_numbers(
    manifest: api_schemas.DocumentManifestResponse, section_number: str
) -> list[str]:
    """Return section_number followed by all its descendants, in document order.

    Traverses the manifest's parent->children adjacency depth-first, so the
    result is exactly the contiguous run of section numbers that a section and
    its nested children occupy within the full document.
    """
    nodes_by_number = {node.number: node for node in manifest.sections}
    if section_number not in nodes_by_number:
        return [section_number]

    ordered: list[str] = []

    def visit(number: str) -> None:
        ordered.append(number)
        node = nodes_by_number.get(number)
        if node is None:
            return
        for child_number in node.children:
            visit(child_number)

    visit(section_number)
    return ordered


def top_level_section_numbers(
    manifest: api_schemas.DocumentManifestResponse,
) -> list[str]:
    """Return the numbers of sections with no parent, in document order."""
    return [node.number for node in manifest.sections if node.parent is None]


async def fetch_joined_sections(
    s3_repo: s3_repository.AbstractGuidanceStorageRepository,
    document_id: uuid.UUID,
    numbers: list[str],
) -> str:
    """Download each numbered section's Markdown and join in the given order.

    Equivalent to slicing the run of sections out of the full /content document.
    S3 errors (e.g. NoSuchKey) propagate for the caller to map.
    """
    parts = [
        (await s3_repo.download_section(document_id, number)).rstrip("\n")
        for number in numbers
    ]
    return "\n\n".join(parts) + "\n"
