"""Pure logic for unpacking an uploaded zip of prototype guides.

The zip is the parser's output directory (scripts/parse_docx_for_s3.py in the
rpa-ai-guidance-hub-api repo) zipped up: manifest.json beside one directory
per document. Zipping the directory itself rather than its contents puts all
of that one level down, so a single top-level directory holding the
manifest is accepted too.

Kept free of any S3/network dependency so it is trivially unit-testable.
"""

import io
import posixpath
import zipfile

MANIFEST_NAME = "manifest.json"

# Clutter archivers add that is never part of a guide.
_IGNORED_PARTS = ("__MACOSX",)
_IGNORED_NAMES = (".DS_Store",)


class InvalidGuidesZipError(ValueError):
    """Raised when an upload is not a zip of prototype guides."""


def unpack(zip_bytes: bytes) -> dict[str, bytes]:
    """Read a zip of prototype guides into keys relative to prototype_guides/.

    Args:
        zip_bytes: The uploaded zip file.

    Returns:
        Each file's bytes, keyed by its path relative to the guides root,
        e.g. "manifest.json" or "<document_id>/assets/<digest>.png".

    Raises:
        InvalidGuidesZipError: If the bytes are not a zip, an entry would
            escape the guides root, or there is no manifest.json either at
            the top level or in a single top-level directory.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        msg = "The upload is not a zip file"
        raise InvalidGuidesZipError(msg) from exc

    with archive:
        files = {
            _safe_name(info.filename): archive.read(info)
            for info in archive.infolist()
            if not info.is_dir() and not _ignored(info.filename)
        }

    root = _root_of(files)

    return {
        name[len(root) :]: body for name, body in files.items() if name.startswith(root)
    }


def _safe_name(name: str) -> str:
    normalised = posixpath.normpath(name.replace("\\", "/"))

    if normalised.startswith(("/", "../")) or normalised == "..":
        msg = f"The zip entry {name!r} points outside the zip"
        raise InvalidGuidesZipError(msg)

    return normalised


def _ignored(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return any(part in _IGNORED_PARTS for part in parts) or parts[-1] in _IGNORED_NAMES


def _root_of(files: dict[str, bytes]) -> str:
    if MANIFEST_NAME in files:
        return ""

    top_levels = {name.split("/", 1)[0] for name in files}
    if len(top_levels) == 1:
        (top_level,) = top_levels
        if f"{top_level}/{MANIFEST_NAME}" in files:
            return f"{top_level}/"

    msg = (
        f"The zip has no {MANIFEST_NAME} at the top level or in a single "
        "top-level directory"
    )
    raise InvalidGuidesZipError(msg)
