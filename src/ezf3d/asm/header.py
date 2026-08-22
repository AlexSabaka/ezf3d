"""The ``ASM BinaryFile8`` header.

Fusion's B-Rep bodies are Autodesk Shape Manager files — ASM is a fork of ACIS
7.0, so the container is a SAB (Standard ACIS Binary) stream with Autodesk's
entity vocabulary.  The trailing ``8`` in the signature is the pointer width:
integers and entity pointers are 64-bit, where stock ACIS SAB uses 32.

Layout::

    b'ASM BinaryFile8'      15 bytes, no terminator
    uint64 version          23200 for 'ASM 232.x'
    uint64 reserved
    uint64 count_a          role not yet established
    uint64 count_b          role not yet established
    str    product          'Autodesk Neutron'
    str    acis_version     'ASM 232.4.0.65535 OSX'
    str    written          'Sat Aug 22 17:33:12 2026'
    double sizebox          model extent hint, e.g. 10.0
    double resabs           absolute tolerance, 1e-6
    double resnor           normal tolerance, 1e-10

The three strings and three doubles are ordinary SAB tokens (``0x07`` and
``0x06``), so the header is really a fixed prelude followed by the first record.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: ``ASM BinaryFile<N>`` where N is the pointer width in bytes.  Fusion writes
#: ``BinaryFile8`` from ASM 232 and ``BinaryFile4`` from ASM 231 and earlier.
ASM_PREFIX = b"ASM BinaryFile"
#: Stock ACIS SAB signature, accepted so an ACIS body parses with the same code.
ACIS_SIGNATURE = b"ACIS BinaryFile"

_TAG_STR = 0x07
_TAG_DOUBLE = 0x06


class AsmError(ValueError):
    """Raised when a stream is not readable ASM/SAB data."""


@dataclass(slots=True)
class AsmHeader:
    """Parsed ASM file header."""

    signature: str
    version: int
    reserved: tuple[int, int, int]
    product: str
    kernel_version: str
    written: str
    sizebox: float
    resabs: float
    resnor: float
    #: Offset of the first entity record.
    body_offset: int
    #: Pointer/integer width in bytes: 8 for ``BinaryFile8``, 4 for ACIS SAB.
    word_size: int = 8

    @property
    def kernel_release(self) -> str:
        """Just the numeric release, e.g. ``232.4.0.65535``."""
        parts = self.kernel_version.split()
        return parts[1] if len(parts) > 1 else self.kernel_version


def read_header(data: bytes) -> AsmHeader:
    """Parse the header of an ASM/SAB stream."""
    if data.startswith(ASM_PREFIX):
        pos = len(ASM_PREFIX)
        digit = data[pos : pos + 1]
        if not digit.isdigit():
            raise AsmError(f"unrecognised ASM signature: {data[:20]!r}")
        word_size = int(digit)
        if word_size not in (4, 8):
            raise AsmError(f"unsupported ASM pointer width: {word_size}")
        pos += 1
    elif data.startswith(ACIS_SIGNATURE):
        word_size = 4
        pos = len(ACIS_SIGNATURE)
    else:
        raise AsmError(f"not an ASM/SAB stream: {data[:16]!r}")
    signature = data[:pos].decode("ascii")

    prelude = struct.Struct("<4Q" if word_size == 8 else "<4I")
    version, *reserved = prelude.unpack_from(data, pos)
    pos += prelude.size

    strings: list[str] = []
    for _ in range(3):
        if data[pos] != _TAG_STR:
            raise AsmError(f"expected a string tag at {pos}, got 0x{data[pos]:02X}")
        n = data[pos + 1]
        strings.append(data[pos + 2 : pos + 2 + n].decode("latin-1"))
        pos += 2 + n

    doubles: list[float] = []
    for _ in range(3):
        if data[pos] != _TAG_DOUBLE:
            raise AsmError(f"expected a double tag at {pos}, got 0x{data[pos]:02X}")
        doubles.append(struct.unpack_from("<d", data, pos + 1)[0])
        pos += 9

    return AsmHeader(
        signature=signature,
        version=version,
        reserved=tuple(reserved),  # type: ignore[arg-type]
        product=strings[0],
        kernel_version=strings[1],
        written=strings[2],
        sizebox=doubles[0],
        resabs=doubles[1],
        resnor=doubles[2],
        body_offset=pos,
        word_size=word_size,
    )
