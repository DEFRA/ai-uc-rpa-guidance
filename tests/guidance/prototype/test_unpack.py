"""Tests for reading an uploaded zip of prototype guides one entry at a time."""

import io
import zipfile
from datetime import UTC, datetime

import pytest

from app.guidance.prototype import unpack

_DOC = "9846c231-d74b-4ca5-b66a-f3e064fed0df"
_VERSION = "ddd2d5a9-b7bb-4a98-b46a-20703a6302a9"


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _guides(prefix: str = "") -> dict[str, bytes]:
    return {
        f"{prefix}{_DOC}/{_VERSION}/content.md": b"# Guide",
        f"{prefix}{_DOC}/assets/digest.png": b"\x89PNG",
    }


def _contents(guides: unpack.GuidesZip) -> dict[str, bytes]:
    contents = {}
    for entry in guides.entries:
        stream = guides.open(entry)
        contents[entry.name] = stream.read()
        stream.close()
    return contents


def _open(zip_bytes: bytes) -> unpack.GuidesZip:
    return unpack.GuidesZip(io.BytesIO(zip_bytes))


class _FailingAfterOpen(io.BytesIO):
    """A zip whose storage starts failing once its index has been read."""

    failing = False

    def read(self, size: int | None = -1) -> bytes:
        if self.failing:
            msg = "connection reset"
            raise OSError(msg)
        return super().read(size)


class TestGuidesZip:
    """Test opening, verifying and streaming a zip of guides."""

    def test_reads_guides_at_the_top_level(self) -> None:
        with _open(_zip(_guides())) as guides:
            assert _contents(guides) == _guides()

    def test_reads_guides_inside_a_single_top_level_directory(self) -> None:
        with _open(_zip(_guides("parsed-guides/"))) as guides:
            assert _contents(guides) == _guides()

    def test_skips_archiver_clutter(self) -> None:
        entries = {
            **_guides(),
            "__MACOSX/._manifest.json": b"junk",
            f"{_DOC}/.DS_Store": b"junk",
        }

        with _open(_zip(entries)) as guides:
            assert {entry.name for entry in guides.entries} == set(_guides())

    def test_verifies_a_good_zip(self) -> None:
        with _open(_zip(_guides())) as guides:
            assert guides.verify() == sum(len(body) for body in _guides().values())

    def test_rejects_bytes_that_are_not_a_zip(self) -> None:
        with pytest.raises(unpack.InvalidGuidesZipError, match="not a zip"):
            _open(b"not a zip")

    def test_ignores_a_manifest_beside_the_guides(self) -> None:
        for prefix in ("", "parsed-guides/"):
            entries = {**_guides(prefix), f"{prefix}manifest.json": b"{}"}

            with _open(_zip(entries)) as guides:
                assert _contents(guides) == _guides()

    def test_accepts_a_zip_with_no_files_as_holding_no_guides(self) -> None:
        with _open(_zip({})) as guides:
            assert guides.entries == []
            assert guides.versions == []
            assert guides.verify() == 0

    def test_accepts_a_zip_holding_only_archiver_clutter_as_empty(self) -> None:
        with _open(_zip({"__MACOSX/._x": b"junk", ".DS_Store": b"junk"})) as guides:
            assert guides.entries == []

    def test_rejects_files_that_are_not_guides(self) -> None:
        zip_bytes = _zip({"notes.txt": b"x", "manifest.json": b"{}"})

        with pytest.raises(unpack.InvalidGuidesZipError, match="no guides"):
            _open(zip_bytes)

    def test_rejects_guides_nested_two_directories_down(self) -> None:
        zip_bytes = _zip(_guides("a/b/"))

        with pytest.raises(unpack.InvalidGuidesZipError, match="no guides"):
            _open(zip_bytes)

    def test_rejects_guides_split_across_top_level_directories(self) -> None:
        zip_bytes = _zip({"one/d/v/notes.md": b"# G", "two/d/v/content.md": b"# G"})

        with pytest.raises(unpack.InvalidGuidesZipError, match="no guides"):
            _open(zip_bytes)

    def test_lists_every_version_with_when_it_was_written(self) -> None:
        other = "0b2f6c8e-2f4b-4bb8-9c43-2d3c1b6c0f11"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for version, when in (
                (_VERSION, (2026, 10, 5, 13, 0, 28)),
                (other, (2026, 10, 6, 9, 30, 0)),
            ):
                info = zipfile.ZipInfo(f"guides/{_DOC}/{version}/content.md", when)
                archive.writestr(info, b"# Guide")
            archive.writestr(f"guides/{_DOC}/assets/digest.png", b"\x89PNG")

        with _open(buffer.getvalue()) as guides:
            found = {
                (v.document_id, v.version_id, v.modified, v.entry.name)
                for v in guides.versions
            }

        assert found == {
            (
                _DOC,
                _VERSION,
                datetime(2026, 10, 5, 13, 0, 28, tzinfo=UTC),
                f"{_DOC}/{_VERSION}/content.md",
            ),
            (
                _DOC,
                other,
                datetime(2026, 10, 6, 9, 30, 0, tzinfo=UTC),
                f"{_DOC}/{other}/content.md",
            ),
        }

    def test_rejects_an_entry_that_escapes_the_root(self) -> None:
        zip_bytes = _zip({**_guides(), "../escape.txt": b"x"})

        with pytest.raises(unpack.InvalidGuidesZipError, match="outside"):
            _open(zip_bytes)

    def test_rejects_too_many_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(unpack, "MAX_ENTRIES", 1)
        zip_bytes = _zip(_guides())

        with pytest.raises(unpack.InvalidGuidesZipError, match="more than the 1"):
            _open(zip_bytes)

    def test_rejects_a_zip_that_unpacks_too_large(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(unpack, "MAX_UNCOMPRESSED_BYTES", 1000)
        # Compresses to a few bytes; unpacks well past the limit.
        zip_bytes = _zip({**_guides(), "bomb.txt": b"0" * 10_000})

        with pytest.raises(unpack.InvalidGuidesZipError, match="unpacks to"):
            _open(zip_bytes)

    def test_verify_rejects_a_corrupt_entry(self) -> None:
        name = f"{_DOC}/{_VERSION}/content.md".encode()
        zip_bytes = bytearray(_zip({name.decode(): b"# " + b"x" * 200}))
        # Damage the entry's compressed data; its index is left intact.
        offset = zip_bytes.index(name) + len(name) + 2
        zip_bytes[offset] ^= 0xFF
        guides = _open(bytes(zip_bytes))

        with pytest.raises(unpack.InvalidGuidesZipError, match="corrupt"):
            guides.verify()

    def test_open_rejects_an_entry_with_a_damaged_header(self) -> None:
        zip_bytes = bytearray(_zip(_guides()))
        # Break the first entry's local header signature.
        zip_bytes[0:4] = b"XXXX"
        guides = _open(bytes(zip_bytes))

        with pytest.raises(unpack.InvalidGuidesZipError, match="corrupt"):
            guides.verify()

    def test_treats_a_storage_read_failure_as_corruption(self) -> None:
        source = _FailingAfterOpen(_zip(_guides()))
        guides = unpack.GuidesZip(source)
        entry = guides.entries[0]
        stream = guides.open(entry)
        source.failing = True

        with pytest.raises(unpack.InvalidGuidesZipError, match="connection reset"):
            stream.read()
