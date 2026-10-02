"""Tests for reading an S3 object by byte range."""

import io
import re
import zipfile
from typing import Any
from unittest.mock import MagicMock

import botocore.exceptions
import pytest

from app.guidance.prototype import s3_range_reader

_BUCKET = "guidance-bucket"
_KEY = "prototype_uploads/upload-1/file-1"


def _client_error(operation: str) -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        error_response={"Error": {"Code": "InternalError", "Message": "boom"}},
        operation_name=operation,
    )


class _FakeS3:
    """Serves one object, honouring Range requests, and counts them."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.ranges: list[str] = []

    def head_object(self, **_kwargs: Any) -> dict[str, int]:
        return {"ContentLength": len(self.data)}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.ranges.append(kwargs["Range"])
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", kwargs["Range"])
        assert match is not None
        start, last = int(match[1]), int(match[2])
        body = MagicMock()
        body.read.return_value = self.data[start : last + 1]
        return {"Body": body}


class TestS3RangeReader:
    """Test the seekable reader."""

    def test_reads_the_whole_object(self) -> None:
        fake = _FakeS3(b"0123456789")

        reader = s3_range_reader.open_object(fake, _BUCKET, _KEY)

        assert reader.read() == b"0123456789"

    def test_seeks_and_reads_a_range(self) -> None:
        fake = _FakeS3(b"0123456789")
        reader = s3_range_reader.S3RangeReader(fake, _BUCKET, _KEY)

        assert reader.seek(-3, io.SEEK_END) == 7
        assert reader.read(10) == b"789"
        assert reader.tell() == 10
        assert reader.read(1) == b""
        assert reader.seek(2) == 2
        assert reader.seek(1, io.SEEK_CUR) == 3
        assert fake.ranges == ["bytes=7-9"]

    def test_refuses_to_seek_before_the_start(self) -> None:
        reader = s3_range_reader.S3RangeReader(_FakeS3(b"abc"), _BUCKET, _KEY)

        with pytest.raises(OSError, match="Cannot seek"):
            reader.seek(-1)

    def test_reads_ahead_rather_than_a_request_per_read(self) -> None:
        fake = _FakeS3(bytes(range(256)) * 10)
        reader = s3_range_reader.open_object(fake, _BUCKET, _KEY)

        for _ in range(100):
            reader.read(10)

        assert len(fake.ranges) == 1

    def test_lets_zipfile_read_an_archive_in_place(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("doc/v/content.md", b"# Guide" * 1000)
        reader = s3_range_reader.open_object(_FakeS3(buffer.getvalue()), _BUCKET, _KEY)

        with zipfile.ZipFile(reader) as archive:
            assert archive.read("doc/v/content.md") == b"# Guide" * 1000

    def test_raises_oserror_when_the_object_cannot_be_looked_up(self) -> None:
        client = MagicMock()
        client.head_object.side_effect = _client_error("HeadObject")

        with pytest.raises(OSError, match="Cannot read"):
            s3_range_reader.S3RangeReader(client, _BUCKET, _KEY)

    def test_raises_oserror_when_a_range_cannot_be_read(self) -> None:
        client = MagicMock()
        client.head_object.return_value = {"ContentLength": 10}
        client.get_object.side_effect = _client_error("GetObject")
        reader = s3_range_reader.S3RangeReader(client, _BUCKET, _KEY)

        with pytest.raises(OSError, match="Cannot read"):
            reader.read(5)
