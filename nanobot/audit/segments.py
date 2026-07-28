"""Exclusive append-only JSONL segment primitives."""

from __future__ import annotations

import errno
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from nanobot.audit.integrity import canonical_json_bytes


class SegmentError(RuntimeError):
    pass


class SegmentSealedError(SegmentError):
    pass


class UncertainAppendError(SegmentError):
    pass


@dataclass(frozen=True, slots=True)
class AppendReceipt:
    start: int
    end: int


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                raise
    finally:
        os.close(descriptor)


class JsonlSegment:
    def __init__(self, path: Path, file: BinaryIO) -> None:
        self.path = path
        self._file = file
        self._sealed = False

    @classmethod
    def create(cls, path: Path, *, mode: int = 0o600) -> JsonlSegment:
        ensure_private_dir(path.parent)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        if os.name == "posix":
            os.fchmod(descriptor, mode)
        file = os.fdopen(descriptor, "wb", buffering=0)
        fsync_directory(path.parent)
        return cls(path, file)

    @property
    def sealed(self) -> bool:
        return self._sealed

    @property
    def offset(self) -> int:
        return self._file.tell()

    def append(self, record: Mapping[str, Any]) -> AppendReceipt:
        if self._sealed:
            raise SegmentSealedError(str(self.path))
        raw = canonical_json_bytes(dict(record)) + b"\n"
        start = self._file.tell()
        try:
            written = self._file.write(raw)
        except Exception:
            self._sealed = True
            raise
        if written != len(raw):
            self._sealed = True
            raise UncertainAppendError(str(self.path))
        return AppendReceipt(start=start, end=start + written)

    def flush(self) -> None:
        if self._sealed:
            raise SegmentSealedError(str(self.path))
        self._file.flush()

    def fsync(self) -> None:
        self.flush()
        os.fsync(self._file.fileno())

    def seal(self) -> None:
        if self._sealed:
            return
        self._sealed = True
        self._file.close()

    def close_uncertain(self) -> None:
        self._sealed = True
        self._file.close()
