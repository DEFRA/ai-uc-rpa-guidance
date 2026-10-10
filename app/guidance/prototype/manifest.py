"""Pure logic for building the prototype guides manifest and resolving versions.

Kept free of any S3/network dependency so it is trivially unit-testable.
``build`` makes the manifest from the versions found in an uploaded zip;
``resolve_version_id`` takes an already-parsed manifest dict (the result of
``json.loads`` on ``manifest.json``) and gives back a version id or ``None``.

The manifest keeps the shape the parser used to write, because the prototype
and the PoC frontend's admin page both read it: a guide per title slug, with
its document id, title, dates, latest version number, and every version's id,
dates, section and image counts and content key.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# A section heading as the parser writes one: two or more hashes, then
# whitespace. One hash is the document's title.
_SECTION_HEADING = re.compile(r"^#{2,}\s", re.MULTILINE)

# A picture as the parser writes one: image syntax with a non-empty address.
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)\s]+\)")

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class GuideVersion:
    """One version of a guide as found in an uploaded zip."""

    document_id: str
    version_id: str
    modified: datetime
    markdown: str


def build(versions: list[GuideVersion]) -> dict[str, Any]:
    """Build the manifest for the guides these versions belong to.

    A guide's versions are ordered by when their content.md was written, oldest
    first, and numbered from 1; the newest is the latest. Versions written at
    the same moment are ordered by id, so the same versions always build the
    same manifest, and are logged, since which is latest is then a guess.

    Args:
        versions: Every version in the zip, of every guide, in any order.

    Returns:
        The manifest, ready to be written as JSON: guides keyed by title slug.
    """
    by_document: dict[str, list[GuideVersion]] = defaultdict(list)
    for version in versions:
        by_document[version.document_id].append(version)

    guides = [_guide(document_id, found) for document_id, found in by_document.items()]
    guides.sort(
        key=lambda guide: (_slug(guide), guide["createdAt"], guide["documentId"])
    )

    manifest: dict[str, Any] = {}
    for guide in guides:
        manifest[_unique(_slug(guide), manifest)] = guide

    return manifest


def _guide(document_id: str, versions: list[GuideVersion]) -> dict[str, Any]:
    ordered = sorted(
        versions, key=lambda version: (version.modified, version.version_id)
    )
    _warn_of_ties(document_id, ordered)

    first, latest = ordered[0], ordered[-1]

    return {
        "documentId": document_id,
        "title": _title_of(latest.markdown),
        "createdAt": first.modified.isoformat(),
        "updatedAt": latest.modified.isoformat(),
        "latestVersion": len(ordered),
        "versions": [
            _version(number, version) for number, version in enumerate(ordered, start=1)
        ],
    }


def _version(number: int, version: GuideVersion) -> dict[str, Any]:
    written = version.modified.isoformat()

    return {
        "version": number,
        "versionId": version.version_id,
        "createdAt": written,
        "updatedAt": written,
        "sections": len(_SECTION_HEADING.findall(version.markdown)),
        "images": len(_IMAGE.findall(version.markdown)),
        "contentUrl": f"{version.document_id}/{version.version_id}/content.md",
    }


def _warn_of_ties(document_id: str, ordered: list[GuideVersion]) -> None:
    times = [version.modified for version in ordered]
    if len(set(times)) < len(times):
        logger.warning(
            "Versions of prototype guide %s share a timestamp; ordered by id, so "
            "its latest version may not be the one last written",
            document_id,
        )


def _title_of(markdown: str) -> str:
    """The title the parser writes as the first line, "# <title>", or ""."""
    first_line = markdown.split("\n", 1)[0]
    if first_line == "#" or first_line.startswith("# "):
        return first_line[1:].strip()
    return ""


def _slug(guide: dict[str, Any]) -> str:
    """The guide's title as a key: lowercase letters and digits, hyphenated.

    A guide with no title is keyed by its document id.
    """
    return _NOT_SLUG.sub("-", guide["title"].lower()).strip("-") or str(
        guide["documentId"]
    )


def _unique(slug: str, taken: dict[str, Any]) -> str:
    """`slug`, or `slug-2`, `slug-3`... if a guide already has it."""
    if slug not in taken:
        return slug

    suffix = 2
    while f"{slug}-{suffix}" in taken:
        suffix += 1
    return f"{slug}-{suffix}"


@dataclass(frozen=True)
class LatestVersion:
    """A guide's latest version, as a manifest names it."""

    document_id: str
    title: str
    version_id: str


def latest_versions(manifest: dict[str, Any]) -> list[LatestVersion]:
    """Return the latest version of every guide in a parsed manifest.

    Args:
        manifest: The parsed contents of prototype_guides/manifest.json.

    Returns:
        One entry per guide, in manifest order. A guide whose latest version
        cannot be resolved is left out.
    """
    found = []
    for guide in manifest.values():
        document_id = str(guide["documentId"])
        version_id = resolve_version_id(manifest, document_id)
        if version_id is not None:
            found.append(
                LatestVersion(
                    document_id=document_id,
                    title=str(guide.get("title", "")),
                    version_id=version_id,
                )
            )
    return found


def resolve_version_id(
    manifest: dict[str, Any],
    document_id: str,
    version_id: str | None = None,
) -> str | None:
    """Resolve a document/version pair against a parsed prototype guides manifest.

    Args:
        manifest: The parsed contents of prototype_guides/manifest.json, keyed
            by guide name (e.g. "claims-guide").
        document_id: The document id to look up, matched against each guide's
            "documentId".
        version_id: An explicit version id to validate against the document's
            version history. If omitted, the document's latest version
            (per its "latestVersion" number) is resolved instead.

    Returns:
        The resolved version id, or None if document_id is not present in the
        manifest at all, or if version_id was given but does not belong to
        that document's version history.
    """
    guide = _find_guide(manifest, document_id)
    if guide is None:
        return None

    versions = guide.get("versions", [])

    if version_id is None:
        latest_version = guide.get("latestVersion")
        return next(
            (
                str(v["versionId"])
                for v in versions
                if v.get("version") == latest_version
            ),
            None,
        )

    return next(
        (str(v["versionId"]) for v in versions if v.get("versionId") == version_id),
        None,
    )


def _find_guide(manifest: dict[str, Any], document_id: str) -> dict[str, Any] | None:
    """Find the manifest entry whose documentId matches document_id.

    Args:
        manifest: The parsed manifest.json contents, keyed by guide name.
        document_id: The document id to search for.

    Returns:
        The matching guide's manifest entry, or None if no guide has this
        document id.
    """
    return next(
        (
            guide
            for guide in manifest.values()
            if guide.get("documentId") == document_id
        ),
        None,
    )
