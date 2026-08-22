"""Zstandard-aware ZIP reader for Fusion 360 archives.

Fusion writes ``.f3d``/``.f3z`` as ZIP files whose entries use compression
method 93 (Zstandard, APPNOTE 6.3.7).  The stdlib :mod:`zipfile` parses the
central directory of such an archive without complaint and only raises from
``ZipFile.open()``, so we let it own directory parsing and take over just the
byte-fetch: seek to the local header, skip it, read ``compress_size`` bytes,
and inflate them ourselves.

That keeps us off monkeypatching ``zipfile.decompressor``-internals, and it
means a future method (LZMA, whatever Fusion picks next) is a two-line change
in :func:`_decompress`.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, BinaryIO

import zstandard

#: ZIP compression method numbers we know how to undo.
ZIP_STORED = 0
ZIP_DEFLATED = 8
ZIP_ZSTANDARD = 93

_LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
_LOCAL_HEADER_SIG = 0x04034B50


class UnsupportedCompressionError(NotImplementedError):
    """Raised for a ZIP entry compressed with a method ezf3d cannot undo."""


class BadArchiveError(ValueError):
    """Raised when an archive is not a readable Fusion container."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One file inside an :class:`F3DArchive`."""

    name: str
    size: int
    compressed_size: int
    method: int
    crc: int

    @property
    def method_name(self) -> str:
        return {
            ZIP_STORED: "stored",
            ZIP_DEFLATED: "deflate",
            ZIP_ZSTANDARD: "zstd",
        }.get(self.method, f"method-{self.method}")


def _decompress(raw: bytes, method: int, expected_size: int, name: str) -> bytes:
    if method == ZIP_STORED:
        return raw
    if method == ZIP_DEFLATED:
        return zlib.decompress(raw, -zlib.MAX_WBITS, max(expected_size, 1))
    if method == ZIP_ZSTANDARD:
        # read_across_frames because Fusion occasionally splits a large entry
        # into several zstd frames; a plain decompress() would stop at the first.
        return zstandard.ZstdDecompressor().stream_reader(raw, read_across_frames=True).read()
    raise UnsupportedCompressionError(
        f"{name!r} uses ZIP compression method {method}, which ezf3d cannot decode"
    )


class F3DArchive:
    """Random-access reader over a Fusion ``.f3d`` / ``.f3z`` archive.

    Accepts a path or any seekable binary stream, so a ``.f3d`` nested inside a
    ``.f3z`` can be opened straight from memory::

        with F3DArchive(path) as pkg:
            inner = F3DArchive(io.BytesIO(pkg.read("root.f3d")))
    """

    def __init__(self, source: str | Path | IO[bytes]) -> None:
        self.source = source
        self._owns_stream = isinstance(source, (str, Path))
        self._stream: BinaryIO
        if self._owns_stream:
            self.path: Path | None = Path(source)  # type: ignore[arg-type]
            self._stream = self.path.open("rb")
        else:
            self.path = None
            self._stream = source  # type: ignore[assignment]
        try:
            self._zf = zipfile.ZipFile(self._stream)
        except zipfile.BadZipFile as exc:  # pragma: no cover - defensive
            if self._owns_stream:
                self._stream.close()
            raise BadArchiveError(f"not a ZIP container: {source}") from exc

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._zf.close()
        if self._owns_stream:
            self._stream.close()

    def __enter__(self) -> F3DArchive:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- directory ---------------------------------------------------------

    def entries(self) -> Iterator[Entry]:
        """Yield every non-directory entry, in central-directory order."""
        for info in self._zf.infolist():
            if info.is_dir():
                continue
            yield Entry(
                name=info.filename,
                size=info.file_size,
                compressed_size=info.compress_size,
                method=info.compress_type,
                crc=info.CRC,
            )

    def namelist(self) -> list[str]:
        return [e.name for e in self.entries()]

    def directories(self) -> list[str]:
        """Directory entries, which carry no payload but shape the layout."""
        return [i.filename for i in self._zf.infolist() if i.is_dir()]

    def __contains__(self, name: str) -> bool:
        try:
            self._zf.getinfo(name)
        except KeyError:
            return False
        return True

    # -- payload -----------------------------------------------------------

    def read(self, name: str, *, verify: bool = True) -> bytes:
        """Return the decompressed bytes of *name*.

        Raises :class:`KeyError` if absent, :class:`UnsupportedCompressionError` if
        the method is unknown, and :class:`BadArchiveError` if *verify* is set and
        the CRC-32 does not match the central directory.
        """
        info = self._zf.getinfo(name)
        if info.compress_type in (ZIP_STORED, ZIP_DEFLATED):
            # Let the stdlib handle what the stdlib handles well.
            data = self._zf.read(name)
        else:
            data = _decompress(
                self._read_raw(info), info.compress_type, info.file_size, info.filename
            )
        if verify and info.CRC and zlib.crc32(data) != info.CRC:
            raise BadArchiveError(f"CRC mismatch for {name!r}")
        if info.file_size and len(data) != info.file_size:
            raise BadArchiveError(
                f"{name!r} decompressed to {len(data)} bytes, expected {info.file_size}"
            )
        return data

    def read_prefix(self, name: str, size: int) -> bytes:
        """Return up to *size* leading bytes of *name* without inflating the rest.

        A single body can be 25 MB of ASM, and reading only its header is
        enough to report the kernel version -- so ``ezf3d info`` costs
        kilobytes regardless of design size.
        """
        info = self._zf.getinfo(name)
        if info.compress_type == ZIP_STORED:
            self._stream.seek(info.header_offset)
            return self._read_raw(info)[:size]
        if info.compress_type == ZIP_DEFLATED:
            with self._zf.open(name) as stream:
                return stream.read(size)
        if info.compress_type == ZIP_ZSTANDARD:
            reader = zstandard.ZstdDecompressor().stream_reader(
                self._read_raw(info), read_across_frames=True
            )
            return reader.read(size)
        raise UnsupportedCompressionError(
            f"{name!r} uses ZIP compression method {info.compress_type}, which ezf3d cannot decode"
        )

    def _read_raw(self, info: zipfile.ZipInfo) -> bytes:
        """Read an entry's compressed bytes, bypassing zipfile's decompressors."""
        self._stream.seek(info.header_offset)
        header = self._stream.read(_LOCAL_HEADER.size)
        sig, *_, name_len, extra_len = _LOCAL_HEADER.unpack(header)
        if sig != _LOCAL_HEADER_SIG:
            raise BadArchiveError(f"no local header for {info.filename!r} at {info.header_offset}")
        # The local header's sizes are unreliable when a data descriptor is in
        # play (general-purpose bit 3), so always trust the central directory.
        self._stream.seek(name_len + extra_len, 1)
        return self._stream.read(info.compress_size)
