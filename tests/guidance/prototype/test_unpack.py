"""Tests for unpacking an uploaded zip of prototype guides."""

import io
import zipfile

import pytest

from app.guidance.prototype import unpack

_DOC = "9846c231-d74b-4ca5-b66a-f3e064fed0df"
_VERSION = "ddd2d5a9-b7bb-4a98-b46a-20703a6302a9"


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _guides(prefix: str = "") -> dict[str, bytes]:
    return {
        f"{prefix}manifest.json": b"{}",
        f"{prefix}{_DOC}/{_VERSION}/content.md": b"# Guide",
        f"{prefix}{_DOC}/assets/digest.png": b"\x89PNG",
    }


class TestUnpack:
    """Test unpack."""

    def test_reads_guides_at_the_top_level(self) -> None:
        assert unpack.unpack(_zip(_guides())) == _guides()

    def test_reads_guides_inside_a_single_top_level_directory(self) -> None:
        assert unpack.unpack(_zip(_guides("parsed-guides/"))) == _guides()

    def test_skips_archiver_clutter(self) -> None:
        entries = {
            **_guides(),
            "__MACOSX/._manifest.json": b"junk",
            f"{_DOC}/.DS_Store": b"junk",
        }

        assert unpack.unpack(_zip(entries)) == _guides()

    def test_rejects_bytes_that_are_not_a_zip(self) -> None:
        with pytest.raises(unpack.InvalidGuidesZipError, match="not a zip"):
            unpack.unpack(b"not a zip")

    def test_rejects_a_zip_without_a_manifest(self) -> None:
        entries = _guides()
        del entries["manifest.json"]

        with pytest.raises(unpack.InvalidGuidesZipError, match="manifest.json"):
            unpack.unpack(_zip(entries))

    def test_rejects_a_manifest_nested_two_directories_down(self) -> None:
        with pytest.raises(unpack.InvalidGuidesZipError, match="manifest.json"):
            unpack.unpack(_zip(_guides("a/b/")))

    def test_rejects_an_entry_that_escapes_the_root(self) -> None:
        entries = {**_guides(), "../escape.txt": b"x"}

        with pytest.raises(unpack.InvalidGuidesZipError, match="outside"):
            unpack.unpack(_zip(entries))
