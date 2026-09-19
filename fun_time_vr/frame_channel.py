from __future__ import annotations

import mmap
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_HEADER = struct.Struct("<QIII")  # sequence (odd while writing), token, width, height
_SEQUENCE = struct.Struct("<Q")
_BYTES_PER_PIXEL = 4


class FrameWriter:
    def __init__(self, path: Path, *, max_pixels: int) -> None:
        self._max_pixels = max_pixels
        size = _HEADER.size + max_pixels * _BYTES_PER_PIXEL
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as created:
            if created.tell() < size:
                created.truncate(size)
        self._file = path.open("r+b")
        self._map = mmap.mmap(self._file.fileno(), size)
        self._sequence = 0
        _HEADER.pack_into(self._map, 0, 0, 0, 0, 0)

    @contextmanager
    def writing(self) -> Iterator[None]:
        self._sequence += 1
        _SEQUENCE.pack_into(self._map, 0, self._sequence)
        try:
            yield
        finally:
            self._sequence += 1
            _SEQUENCE.pack_into(self._map, 0, self._sequence)

    def write(self, token: int, width: int, height: int, pixels: bytes) -> None:
        if width * height > self._max_pixels or len(pixels) != width * height * _BYTES_PER_PIXEL:
            raise ValueError(f"a {width}x{height} frame does not fit this channel")
        with self.writing():
            self._map[_HEADER.size:_HEADER.size + len(pixels)] = pixels
            _HEADER.pack_into(self._map, 0, self._sequence, token, width, height)

    def close(self) -> None:
        self._map.close()
        self._file.close()


class FrameReader:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file = None
        self._map: mmap.mmap | None = None
        self._read = 0

    def latest(self, token: int) -> tuple[int, int, bytes] | None:
        frames = self._opened()
        if frames is None:
            return None
        sequence, drawn_for, width, height = _HEADER.unpack_from(frames, 0)
        end = _HEADER.size + width * height * _BYTES_PER_PIXEL
        if sequence % 2 or sequence == self._read or drawn_for != token or end > len(frames):
            return None
        pixels = frames[_HEADER.size:end]
        if _SEQUENCE.unpack_from(frames, 0)[0] != sequence:
            return None
        self._read = sequence
        return width, height, pixels

    def _opened(self) -> mmap.mmap | None:
        if self._map is None:
            try:
                if self._path.stat().st_size <= _HEADER.size:
                    return None
                self._file = self._path.open("rb")
                self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            except OSError:
                self.close()
                return None
        return self._map

    def close(self) -> None:
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._file is not None:
            self._file.close()
            self._file = None
