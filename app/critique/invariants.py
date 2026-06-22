"""Programmatic AC5 backstop: detect structural drift between revisions.

The writer prompt forbids structural changes; these checks catch violations
the prompt did not prevent. Violations are surfaced as warnings, not errors —
the POC reports them for human judgement rather than rejecting the revision.
"""

import re
from collections import Counter

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)")
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)")
_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)


def _image_refs(text: str) -> Counter[str]:
    return Counter(_IMAGE_RE.findall(text))


def _link_urls(text: str) -> Counter[str]:
    return Counter(_LINK_RE.findall(text))


def _heading_levels(text: str) -> list[int]:
    return [len(hashes) for hashes in _HEADING_RE.findall(text)]


def check_invariants(original: str, revised: str) -> list[str]:
    """Compare a revision against the original document.

    Returns human-readable warnings for: missing/reduced image references,
    missing/reduced link URLs, and changes to the heading structure (count
    and level sequence — heading wording is allowed to change).
    """
    warnings: list[str] = []

    original_images = _image_refs(original)
    revised_images = _image_refs(revised)
    missing_images = original_images - revised_images
    for ref, count in sorted(missing_images.items()):
        warnings.append(
            f"Image reference missing from revision ({count}x): {ref}",
        )

    original_links = _link_urls(original)
    revised_links = _link_urls(revised)
    missing_links = original_links - revised_links
    for url, count in sorted(missing_links.items()):
        warnings.append(
            f"Link URL missing from revision ({count}x): {url}",
        )

    original_headings = _heading_levels(original)
    revised_headings = _heading_levels(revised)
    if original_headings != revised_headings:
        warnings.append(
            "Heading structure changed: "
            f"{len(original_headings)} headings {original_headings} -> "
            f"{len(revised_headings)} headings {revised_headings}",
        )

    return warnings
