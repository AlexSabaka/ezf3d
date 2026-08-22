"""A minimal PNG encoder.

Writing PNG is a zlib stream plus four chunk headers, which is small enough
that pulling in an imaging library to do it would cost more than it saves —
and it keeps the renderer's promise of no native dependencies beyond numpy.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COLOUR_RGB = 2
_COLOUR_RGBA = 6


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode(pixels: np.ndarray, *, level: int = 6) -> bytes:
    """Encode an ``(h, w, 3)`` or ``(h, w, 4)`` uint8 array as a PNG."""
    if pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError(f"expected (h, w, 3) or (h, w, 4), got {pixels.shape}")
    data = np.ascontiguousarray(pixels, dtype=np.uint8)
    height, width, channels = data.shape

    # Filter type 0 (None) in front of every scanline.
    rows = np.zeros((height, width * channels + 1), dtype=np.uint8)
    rows[:, 1:] = data.reshape(height, width * channels)

    header = struct.pack(
        ">IIBBBBB",
        width,
        height,
        8,
        _COLOUR_RGB if channels == 3 else _COLOUR_RGBA,
        0,
        0,
        0,
    )
    return (
        _SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows.tobytes(), level))
        + _chunk(b"IEND", b"")
    )


def write(path, pixels: np.ndarray, *, level: int = 6) -> int:
    """Write *pixels* to *path*; returns the number of bytes written."""
    payload = encode(pixels, level=level)
    with open(path, "wb") as handle:
        handle.write(payload)
    return len(payload)
