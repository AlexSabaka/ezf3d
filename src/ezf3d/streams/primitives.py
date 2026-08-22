"""Primitive readers for Fusion's "Neutron" binary serialization.

Every ``.dat`` stream in a Fusion document uses the same handful of encodings:

===============  ==========================================================
``str8``         ``uint32`` byte count, then that many bytes of ASCII/UTF-8
``wstr``         ``uint32`` *character* count, then 2x that many bytes UTF-16LE
scalars          little-endian ``u8/u16/u32/u64/i32/i64/f32/f64``
guid             a ``wstr`` of exactly 36 characters
===============  ==========================================================

There are no type tags on the wire: a reader has to know the schema of the
record it is standing on.  That is why :class:`Reader` is deliberately thin and
why :func:`scan_strings` exists — when a schema is not yet known, scanning for
self-consistent strings is how it gets discovered.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

_U8 = struct.Struct("<B")
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_I32 = struct.Struct("<i")
_I64 = struct.Struct("<q")
_F32 = struct.Struct("<f")
_F64 = struct.Struct("<d")

GUID_LEN = 36


class StreamError(ValueError):
    """Raised when a stream does not match the expected shape."""


class Reader:
    """A cursor over a ``bytes`` buffer with Neutron's primitive readers."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    # -- cursor ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.data)

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    @property
    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def seek(self, pos: int) -> None:
        self.pos = pos

    def skip(self, n: int) -> None:
        self.pos += n

    def _take(self, size: int) -> int:
        start = self.pos
        end = start + size
        if end > len(self.data):
            raise StreamError(f"read of {size} bytes at {start} runs past end ({len(self.data)})")
        self.pos = end
        return start

    # -- scalars -----------------------------------------------------------

    def u8(self) -> int:
        return _U8.unpack_from(self.data, self._take(1))[0]

    def u16(self) -> int:
        return _U16.unpack_from(self.data, self._take(2))[0]

    def u32(self) -> int:
        return _U32.unpack_from(self.data, self._take(4))[0]

    def u64(self) -> int:
        return _U64.unpack_from(self.data, self._take(8))[0]

    def i32(self) -> int:
        return _I32.unpack_from(self.data, self._take(4))[0]

    def i64(self) -> int:
        return _I64.unpack_from(self.data, self._take(8))[0]

    def f32(self) -> float:
        return _F32.unpack_from(self.data, self._take(4))[0]

    def f64(self) -> float:
        return _F64.unpack_from(self.data, self._take(8))[0]

    def raw(self, n: int) -> bytes:
        start = self._take(n)
        return self.data[start : start + n]

    # -- strings -----------------------------------------------------------

    def str8(self) -> str:
        n = self.u32()
        return self.raw(n).decode("utf-8", "replace")

    def wstr(self) -> str:
        n = self.u32()
        return self.raw(2 * n).decode("utf-16-le", "replace")

    def guid(self) -> str:
        value = self.wstr()
        if len(value) != GUID_LEN:
            raise StreamError(f"expected a 36-char GUID at {self.pos}, got {value!r}")
        return value

    # -- composites --------------------------------------------------------

    def counted(self, read_one, count: int | None = None) -> list:
        """Read a ``uint32`` count (unless given) followed by that many items."""
        n = self.u32() if count is None else count
        return [read_one() for _ in range(n)]

    def str8_u32_table(self) -> dict[str, int]:
        """Read ``count x (str8, uint32)`` — Fusion's schema-version tables."""
        return dict(self.counted(lambda: (self.str8(), self.u32())))


# -- discovery helpers -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoundString:
    """A string located by :func:`scan_strings`."""

    offset: int
    kind: str  # 'str8' | 'wstr'
    value: str

    @property
    def end(self) -> int:
        n = len(self.value)
        return self.offset + 4 + (2 * n if self.kind == "wstr" else n)


def _plausible(value: str, min_len: int) -> bool:
    if len(value) < min_len:
        return False
    return all(c == "\t" or 32 <= ord(c) < 127 for c in value)


def scan_strings(
    data: bytes,
    *,
    start: int = 0,
    end: int | None = None,
    min_len: int = 3,
    max_len: int = 512,
) -> Iterator[FoundString]:
    """Walk *data* yielding every self-consistent ``str8``/``wstr``.

    Used by the forensic ``ezf3d raw`` command and by schema archaeology on
    streams we have not fully decoded yet.  Greedy and non-overlapping: once a
    string is accepted, scanning resumes after it.
    """
    view = memoryview(data)
    limit = len(data) if end is None else min(end, len(data))
    pos = start
    while pos + 4 <= limit:
        n = _U32.unpack_from(view, pos)[0]
        if min_len <= n <= max_len:
            if pos + 4 + 2 * n <= limit:
                raw = bytes(view[pos + 4 : pos + 4 + 2 * n])
                if all(raw[i * 2 + 1] == 0 for i in range(n)):
                    text = raw.decode("utf-16-le", "replace")
                    if _plausible(text, min_len):
                        yield FoundString(pos, "wstr", text)
                        pos += 4 + 2 * n
                        continue
            if pos + 4 + n <= limit:
                text = bytes(view[pos + 4 : pos + 4 + n]).decode("latin-1")
                if _plausible(text, min_len):
                    yield FoundString(pos, "str8", text)
                    pos += 4 + n
                    continue
        pos += 1
