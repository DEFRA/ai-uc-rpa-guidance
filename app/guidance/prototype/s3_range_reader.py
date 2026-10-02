"""A seekable, read-only file over an S3 object, fetched by byte range.

A zip's index sits at its end, so reading one means seeking about the file;
this lets zipfile do that against the object where it lies instead of
downloading it first. Wrapped in a buffered reader, each range request reads
ahead, so zipfile's many small reads do not each become a request.
"""

import io
from typing import Any

import botocore.exceptions

# How much each range request reads ahead.
READ_AHEAD_BYTES = 1024 * 1024


class S3RangeReader(io.RawIOBase):
    """Reads an S3 object by byte range. S3 failures are raised as OSError."""

    def __init__(self, s3_client: Any, bucket: str, key: str) -> None:
        """Look the object up, ready to read it.

        Args:
            s3_client: A boto3 S3 client.
            bucket: The object's bucket.
            key: The object's key.

        Raises:
            OSError: If the object cannot be looked up.
        """
        super().__init__()
        self._s3 = s3_client
        self._bucket = bucket
        self._key = key
        self._position = 0

        try:
            head = self._s3.head_object(Bucket=bucket, Key=key)
        except (
            botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError,
        ) as exc:
            msg = f"Cannot read s3://{bucket}/{key}: {exc}"
            raise OSError(msg) from exc

        self.size: int = head["ContentLength"]

    def readable(self) -> bool:
        """Whether it can be read; always.

        Returns:
            True.
        """
        return True

    def seekable(self) -> bool:
        """Whether it can be seeked; always.

        Returns:
            True.
        """
        return True

    def tell(self) -> int:
        """Where the next read starts.

        Returns:
            The current offset.
        """
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Move where the next read starts.

        Args:
            offset: The offset, relative to `whence`.
            whence: io.SEEK_SET, io.SEEK_CUR or io.SEEK_END.

        Returns:
            The new offset.

        Raises:
            OSError: If the offset would be before the start of the object.
        """
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self.size}[
            whence
        ]
        position = base + offset
        if position < 0:
            msg = f"Cannot seek to {position} in s3://{self._bucket}/{self._key}"
            raise OSError(msg)

        self._position = position
        return position

    def readinto(self, buffer: Any) -> int:
        """Read the next bytes into `buffer` with one range request.

        Args:
            buffer: A writable buffer to fill.

        Returns:
            How many bytes were read; 0 at the end of the object.

        Raises:
            OSError: If the range cannot be read.
        """
        wanted = min(len(buffer), self.size - self._position)
        if wanted <= 0:
            return 0

        last = self._position + wanted - 1
        try:
            response = self._s3.get_object(
                Bucket=self._bucket,
                Key=self._key,
                Range=f"bytes={self._position}-{last}",
            )
            data: bytes = response["Body"].read()
        except (
            botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError,
        ) as exc:
            msg = f"Cannot read s3://{self._bucket}/{self._key}: {exc}"
            raise OSError(msg) from exc

        buffer[: len(data)] = data
        self._position += len(data)
        return len(data)


def open_object(s3_client: Any, bucket: str, key: str) -> io.BufferedReader:
    """Open an S3 object as a seekable, read-ahead binary file.

    Args:
        s3_client: A boto3 S3 client.
        bucket: The object's bucket.
        key: The object's key.

    Returns:
        A buffered, seekable reader over the object.

    Raises:
        OSError: If the object cannot be looked up.
    """
    return io.BufferedReader(
        S3RangeReader(s3_client, bucket, key), buffer_size=READ_AHEAD_BYTES
    )
