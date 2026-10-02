"""Reading an uploaded zip of prototype guides one entry at a time.

The zip is the parser's output directory (scripts/parse_docx_for_s3.py in the
rpa-ai-guidance-hub-api repo) zipped up: manifest.json beside one directory
per document. Zipping the directory itself rather than its contents puts all
of that one level down, so a single top-level directory holding the
manifest is accepted too.

Nothing here holds the zip or its contents in memory. It reads from any
seekable binary file -- in practice the uploaded object read by byte range
from S3 -- and decompresses one entry at a time as it is streamed. Any problem
reading the zip, from the zip itself or from underneath it, is treated as the
zip being corrupt.
"""

import posixpath
import zipfile
import zlib
from dataclasses import dataclass
from typing import IO, Self

MANIFEST_NAME = "manifest.json"

# Ten times the guides as first uploaded (443 files, 37 MB unzipped), so a
# zip that expands far beyond its size is refused before anything is read.
MAX_ENTRIES = 5_000
MAX_UNCOMPRESSED_BYTES = 400_000_000

_VERIFY_CHUNK_BYTES = 1024 * 1024

# What reading a damaged, encrypted or unsupported entry, or the storage under
# it, can raise.
_UNREADABLE = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    OSError,
    RuntimeError,
    NotImplementedError,
)

# Clutter archivers add that is never part of a guide.
_IGNORED_PARTS = ("__MACOSX",)
_IGNORED_NAMES = (".DS_Store",)


class InvalidGuidesZipError(ValueError):
    """Raised when an upload is not a zip of prototype guides."""


@dataclass(frozen=True)
class GuideEntry:
    """One file in the zip, named by its path relative to prototype_guides/."""

    name: str
    info: zipfile.ZipInfo


class GuidesZip:
    """An open zip of prototype guides, checked from its index alone.

    Opening it reads only the zip's central directory: the manifest is where
    expected, no entry escapes the guides root, and the entry count and total
    unzipped size are within bounds. `verify` then reads every entry through,
    and `open` streams one at a time.
    """

    def __init__(self, fileobj: IO[bytes]) -> None:
        """Open the zip and check its index.

        Args:
            fileobj: The zip, seekable and readable.

        Raises:
            InvalidGuidesZipError: If it is not a readable zip, or its index
                fails a check.
        """
        try:
            self._archive = zipfile.ZipFile(fileobj)
        except _UNREADABLE as exc:
            msg = "The upload is not a zip file"
            raise InvalidGuidesZipError(msg) from exc

        try:
            self.entries = _entries_of(self._archive)
        except InvalidGuidesZipError:
            self._archive.close()
            raise

    def __enter__(self) -> Self:
        """Use as a context manager, closing the zip on exit.

        Returns:
            This zip.
        """
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the zip."""
        self._archive.close()

    def verify(self) -> None:
        """Read every entry through, checking each one's CRC, keeping nothing.

        Raises:
            InvalidGuidesZipError: If any entry cannot be read.
        """
        for entry in self.entries:
            stream = self.open(entry)
            try:
                while stream.read(_VERIFY_CHUNK_BYTES):
                    pass
            finally:
                stream.close()

    def open(self, entry: GuideEntry) -> EntryStream:
        """Stream one entry, decompressing as it is read.

        Args:
            entry: An entry from `entries`.

        Returns:
            A binary stream whose read errors raise InvalidGuidesZipError.

        Raises:
            InvalidGuidesZipError: If the entry cannot be opened.
        """
        try:
            return EntryStream(self._archive.open(entry.info))
        except _UNREADABLE as exc:
            raise _corrupt(exc) from exc


class EntryStream:
    """One entry's stream, reporting any read error as the zip being corrupt."""

    def __init__(self, stream: IO[bytes]) -> None:
        """Wrap an entry's decompressing stream.

        Args:
            stream: The stream zipfile opened for the entry.
        """
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        """Read and decompress up to `size` bytes.

        Args:
            size: How many bytes at most; -1 for the rest.

        Returns:
            The bytes read; empty at the end of the entry.

        Raises:
            InvalidGuidesZipError: If the entry or the storage under it
                cannot be read.
        """
        try:
            return self._stream.read(size)
        except _UNREADABLE as exc:
            raise _corrupt(exc) from exc

    def close(self) -> None:
        """Close the entry's stream."""
        self._stream.close()


def _corrupt(exc: BaseException) -> InvalidGuidesZipError:
    return InvalidGuidesZipError(f"The zip is corrupt or unreadable: {exc}")


def _entries_of(archive: zipfile.ZipFile) -> list[GuideEntry]:
    infos = [
        info
        for info in archive.infolist()
        if not info.is_dir() and not _ignored(info.filename)
    ]

    if len(infos) > MAX_ENTRIES:
        msg = f"The zip has {len(infos)} files, more than the {MAX_ENTRIES} allowed"
        raise InvalidGuidesZipError(msg)

    unzipped = sum(info.file_size for info in infos)
    if unzipped > MAX_UNCOMPRESSED_BYTES:
        msg = (
            f"The zip unpacks to {unzipped} bytes, more than the "
            f"{MAX_UNCOMPRESSED_BYTES} allowed"
        )
        raise InvalidGuidesZipError(msg)

    named = {_safe_name(info.filename): info for info in infos}
    root = _root_of(named)

    return [
        GuideEntry(name=name[len(root) :], info=info)
        for name, info in named.items()
        if name.startswith(root)
    ]


def _safe_name(name: str) -> str:
    normalised = posixpath.normpath(name.replace("\\", "/"))

    if normalised.startswith(("/", "../")) or normalised == "..":
        msg = f"The zip entry {name!r} points outside the zip"
        raise InvalidGuidesZipError(msg)

    return normalised


def _ignored(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return any(part in _IGNORED_PARTS for part in parts) or parts[-1] in _IGNORED_NAMES


def _root_of(names: dict[str, zipfile.ZipInfo]) -> str:
    if MANIFEST_NAME in names:
        return ""

    top_levels = {name.split("/", 1)[0] for name in names}
    if len(top_levels) == 1:
        (top_level,) = top_levels
        if f"{top_level}/{MANIFEST_NAME}" in names:
            return f"{top_level}/"

    msg = (
        f"The zip has no {MANIFEST_NAME} at the top level or in a single "
        "top-level directory"
    )
    raise InvalidGuidesZipError(msg)
