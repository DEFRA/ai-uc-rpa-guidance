"""Pure logic for resolving prototype guide versions from a parsed manifest.

Kept free of any S3/network dependency so it is trivially unit-testable:
callers pass in an already-parsed manifest dict (the result of
``json.loads`` on ``manifest.json``) and get back a version id or ``None``.
"""

from typing import Any


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
