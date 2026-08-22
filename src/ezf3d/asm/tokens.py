"""SAB token grammar for ``ASM BinaryFile8`` streams.

Records are flat token sequences terminated by ``RECORD_END``.  Nested geometry
(spline curves and surfaces) is wrapped in ``SUBTYPE_START`` / ``SUBTYPE_END``
brackets, which nest and always balance — so a record's extent is knowable
without understanding the geometry inside it.

Widths follow the file's :attr:`~ezf3d.asm.header.AsmHeader.word_size`: 8 bytes
for ASM ``BinaryFile8``, 4 for stock ACIS SAB.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from enum import IntEnum

from ezf3d.asm.header import AsmError


class Tag(IntEnum):
    """SAB type tags."""

    NO_TYPE = 0x00
    BYTE = 0x01
    CHAR = 0x02
    SHORT = 0x03
    INT = 0x04
    FLOAT = 0x05
    DOUBLE = 0x06
    STR = 0x07
    STR2 = 0x08
    STR3 = 0x09
    BOOL_FALSE = 0x0A
    BOOL_TRUE = 0x0B
    POINTER = 0x0C
    ENTITY_TYPE = 0x0D
    ENTITY_TYPE_EX = 0x0E
    SUBTYPE_START = 0x0F
    SUBTYPE_END = 0x10
    RECORD_END = 0x11
    LITERAL_STR = 0x12
    POSITION = 0x13
    DIRECTION = 0x14
    ENUM = 0x15


#: Tags whose payload is a plain string.
STRING_TAGS = frozenset(
    {Tag.STR, Tag.STR2, Tag.STR3, Tag.ENTITY_TYPE, Tag.ENTITY_TYPE_EX, Tag.LITERAL_STR}
)
#: Tags that carry no payload at all.
EMPTY_TAGS = frozenset(
    {Tag.NO_TYPE, Tag.BOOL_FALSE, Tag.BOOL_TRUE, Tag.SUBTYPE_START, Tag.SUBTYPE_END}
)

#: A record is one entity: its type name (first ``ENTITY_TYPE`` token) plus its
#: fields, as ``(tag, value)`` pairs.
Token = tuple[int, object]
Record = list[Token]

_F64 = struct.Struct("<d")
_F32 = struct.Struct("<f")
_I16 = struct.Struct("<h")
_VEC = struct.Struct("<3d")


class TokenError(AsmError):
    """Raised on an unrecognised tag or a truncated token."""


def tokenize(data: bytes, start: int, word_size: int = 8) -> Iterator[Record]:
    """Yield one :data:`Record` per entity in *data*, starting at *start*.

    Raises :class:`TokenError` on an unknown tag rather than guessing, so a
    silent misparse cannot masquerade as a valid model.
    """
    word = struct.Struct("<q" if word_size == 8 else "<i")
    word_unpack, word_size_ = word.unpack_from, word.size
    end = len(data)
    pos = start
    record: Record = []
    append = record.append

    while pos < end:
        tag = data[pos]
        pos += 1

        if tag == Tag.RECORD_END:
            yield record
            record = []
            append = record.append
            continue
        if tag in (Tag.POINTER, Tag.INT, Tag.ENUM):
            append((tag, word_unpack(data, pos)[0]))
            pos += word_size_
        elif tag == Tag.DOUBLE:
            append((tag, _F64.unpack_from(data, pos)[0]))
            pos += 8
        elif tag in (Tag.STR, Tag.ENTITY_TYPE, Tag.ENTITY_TYPE_EX):
            n = data[pos]
            append((tag, data[pos + 1 : pos + 1 + n].decode("latin-1")))
            pos += 1 + n
        elif tag in (Tag.POSITION, Tag.DIRECTION):
            append((tag, _VEC.unpack_from(data, pos)))
            pos += 24
        elif tag in EMPTY_TAGS:
            append((tag, None))
        elif tag == Tag.BYTE:
            append((tag, data[pos]))
            pos += 1
        elif tag == Tag.CHAR:
            append((tag, data[pos] - 256 if data[pos] > 127 else data[pos]))
            pos += 1
        elif tag == Tag.SHORT:
            append((tag, _I16.unpack_from(data, pos)[0]))
            pos += 2
        elif tag == Tag.FLOAT:
            append((tag, _F32.unpack_from(data, pos)[0]))
            pos += 4
        elif tag == Tag.STR2:
            n = _I16.unpack_from(data, pos)[0]
            append((tag, data[pos + 2 : pos + 2 + n].decode("latin-1")))
            pos += 2 + n
        elif tag in (Tag.STR3, Tag.LITERAL_STR):
            n = struct.unpack_from("<I", data, pos)[0]
            append((tag, data[pos + 4 : pos + 4 + n].decode("latin-1")))
            pos += 4 + n
        else:
            raise TokenError(
                f"unknown SAB tag 0x{tag:02X} at offset {pos - 1} "
                f"(context {data[max(0, pos - 8) : pos + 8].hex(' ')})"
            )

    if record:
        yield record

