"""Design parameters: the names, roles, units, expressions and values.

Every dimension a designer types goes into one of these.  ``ezf3d bodies`` can
say a face is a cylinder of radius 1.5 cm; this module is what says the radius
came from ``d27``, whose expression is ``1 mm``, and which feature slot it
fills.

A design keeps them in two objects that sit next to each other in the bulk
stream:

**The manager** holds a reference to the table, then one reference per
parameter in id order.

**The table** maps name to object: ``u32 count``, then that many entries of
``wstr name, 0x01, u64 object id, u16 0``.  It is the authoritative list --
every name a design has, including any the user typed.

**Each parameter is its own object**, and its layout is fixed after a 31-byte
preamble::

    u32 number                          (at +16, the N in the auto name dN)
    ...
    wstr expression                     (at +31, e.g. "300 mm")
    <padding>                           (nine bytes, ten before revision 489)
    wstr role, wstr comment, wstr unit, wstr name
    f64 value
    0x00, 0x01, u64 manager, 0x00, 0x00
    str8 revision                       (of the record, not the stream)

Three redundancies make the reading checkable rather than merely plausible.
The name in the record equals the name the table filed it under; the ``u32``
at +16 equals the digits of that name; and the back-reference after the value
is the same manager object for every parameter of a document.  All three hold
for **1,193 of 1,193** parameters across the four samples.

**Values are in Fusion's internal units** -- centimetres and radians -- and the
expression carries the unit the designer typed.  That is checkable too: where
an expression is a literal, converting it by :data:`UNIT_FACTORS` must give the
stored value, and it does for every literal in the samples.  A parameter whose
expression is a *formula* is reported as written; ezf3d does not evaluate it.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from ezf3d.streams.primitives import scan_strings
from ezf3d.streams.segment import BulkObject, Segment

#: Bytes before a parameter record's expression.  Constant across the four
#: bulk-stream revisions on hand (299, 373, 397, 489) and the five record
#: revisions inside them (301, 312, 327, 369, 387).
PREAMBLE = 31

#: Offset of the ``u32`` that repeats the number in an auto-generated name.
NUMBER_AT = 16

#: Most padding tolerated between the expression and the role.  Nine bytes in
#: revision 489, ten in the others; the search is bounded rather than fixed so
#: a further revision does not need new code to be *read* -- only to be
#: trusted, which the name check decides.
_MAX_PADDING = 16

#: A reference: the byte ``0x01`` followed by a little-endian object id.
_REFERENCE = 0x01

#: Longest string treated as a name or a unit while walking the table.
_MAX_STRING = 512

#: How Fusion names a parameter it generated itself.  The ``_1`` suffix appears
#: on parameters that arrived with an XREF'd component.
AUTO_NAME_RE = re.compile(r"d(\d+)(?:_\d+)?$")

#: An expression that is a plain number with an optional unit, which is the
#: case :func:`Parameters.literal_check` can verify against the stored value.
LITERAL_RE = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$")

#: How many of a unit make one internal unit.  Lengths are stored in
#: centimetres and angles in radians, so ``mm`` is 10 and ``deg`` is 180/pi.
#:
#: ``mm``, ``deg`` and the empty unit are the ones the samples exercise -- 1,185
#: literal expressions between them -- and ``in`` is pinned by a single formula,
#: ``1.5 in / 2``, stored as 1.905.  The rest follow from the same two internal
#: units and are offered untested; an unknown unit converts to ``None`` rather
#: than to a guess.
UNIT_FACTORS: dict[str, float] = {
    "": 1.0,
    "cm": 1.0,
    "mm": 10.0,
    "m": 0.01,
    "km": 1e-5,
    "in": 1.0 / 2.54,
    "ft": 1.0 / 30.48,
    "rad": 1.0,
    "deg": 180.0 / math.pi,
    "grad": 200.0 / math.pi,
}

#: Relative tolerance for the literal check.  Fusion writes the value as a
#: ``f64`` computed from the same literal, so agreement is near-exact.
LITERAL_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class Parameter:
    """One parameter of a design."""

    #: Object id -- also its position in creation order.
    oid: int
    #: What the Parameters dialog calls it: ``d27``, or whatever the user typed.
    name: str
    #: The ``u32`` at :data:`NUMBER_AT`, which repeats the digits of an auto name.
    number: int
    #: The slot it fills in the feature that owns it -- ``AlongDistance``,
    #: ``TaperAngle``, ``Radius`` -- or the sketch dimension's own name.
    role: str
    #: Unit as the designer sees it; empty for a count or a ratio.
    unit: str
    #: What the designer typed.  May be a formula naming other parameters.
    expression: str
    #: **Internal units**: centimetres for lengths, radians for angles.
    value: float
    comment: str = ""
    #: Schema revision Fusion stamped on this record.
    revision: str = ""

    @property
    def is_auto(self) -> bool:
        """True when Fusion generated the name rather than the user typing one."""
        return AUTO_NAME_RE.fullmatch(self.name) is not None

    @property
    def display(self) -> float | None:
        """:attr:`value` in :attr:`unit`, or ``None`` for a unit ezf3d has no factor for."""
        factor = UNIT_FACTORS.get(self.unit)
        return None if factor is None else self.value * factor


@dataclass(slots=True)
class Parameters:
    """Every parameter a design segment declares."""

    #: Object id of the name table.
    table: int = 0
    #: Object id of the manager, which every parameter points back to.
    manager: int = 0
    #: Entries the table declares.
    declared: int = 0
    #: The parameters, in creation order.
    values: list[Parameter] = field(default_factory=list)
    #: Names the table declares whose record did not read.  Empty in every
    #: sample; named rather than dropped when it is not.
    unreadable: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self):
        return iter(self.values)

    def by_name(self) -> dict[str, Parameter]:
        return {parameter.name: parameter for parameter in self.values}

    def literal_check(self) -> tuple[int, tuple[str, ...]]:
        """``(checked, disagreeing names)`` over parameters with literal expressions.

        Converts each literal expression by :data:`UNIT_FACTORS` and compares it
        with the stored value.  This is what pins the claim that values are
        centimetres and radians: it is derived from two fields Fusion wrote
        independently, so agreement is evidence and not a restatement.
        """
        checked = 0
        wrong: list[str] = []
        for parameter in self.values:
            match = LITERAL_RE.fullmatch(parameter.expression)
            if match is None:
                continue
            factor = UNIT_FACTORS.get(match.group(2) or parameter.unit)
            if factor is None:
                continue
            checked += 1
            expected = float(match.group(1)) / factor
            if abs(parameter.value - expected) > LITERAL_TOLERANCE * max(1.0, abs(expected)):
                wrong.append(parameter.name)
        return checked, tuple(wrong)


def _wstr_at(body: bytes, pos: int, limit: int) -> tuple[str, int] | None:
    """A ``wstr`` at *pos* and the offset past it, or ``None``.

    Empty is a legitimate value -- most parameters carry no comment and a count
    carries no unit -- so a zero length reads as the empty string.
    """
    if pos < 0 or pos + 4 > limit:
        return None
    count = struct.unpack_from("<I", body, pos)[0]
    if count > _MAX_STRING or pos + 4 + 2 * count > limit:
        return None
    raw = body[pos + 4 : pos + 4 + 2 * count]
    if any(raw[2 * i + 1] for i in range(count)):
        return None
    return raw.decode("utf-16-le"), pos + 4 + 2 * count


def _is_label(text: str) -> bool:
    return bool(text) and all(char >= " " for char in text)


def _walk_table(body: bytes, item: BulkObject, first: int) -> list[tuple[str, int]] | None:
    """The ``name -> object id`` entries of a table whose first name is at *first*.

    ``None`` when the walk does not complete, which is what disqualifies every
    object that is not the parameter table.
    """
    if first - 4 < item.offset:
        return None
    count = struct.unpack_from("<I", body, first - 4)[0]
    if not 1 <= count <= (item.size // 12):
        return None
    entries: list[tuple[str, int]] = []
    pos = first
    for _ in range(count):
        found = _wstr_at(body, pos, item.end)
        if found is None or not _is_label(found[0]):
            return None
        name, after = found
        if after + 11 > item.end or body[after] != _REFERENCE:
            return None
        entries.append((name, struct.unpack_from("<Q", body, after + 1)[0]))
        pos = after + 11
    return entries


def find_table(segment: Segment) -> tuple[BulkObject, list[tuple[str, int]]] | None:
    """The parameter name table, or ``None`` for a design that has none.

    Found by shape rather than by position: the table is the object whose first
    wide string is preceded by a count that walks cleanly to that many
    ``name, reference`` entries.  Two of the ``.f3z`` package's members hold no
    such object and no parameters either, which is the honest answer for a
    document assembled out of imported bodies.
    """
    body = segment.bulk.body
    items = segment.objects()
    if not items:
        return None

    # One linear pass: the bulk stream is megabytes and per-object scanning is
    # what would make this command expensive.
    starts = [item.offset for item in items]
    first_wide: dict[int, int] = {}
    index = 0
    for found in scan_strings(body, min_len=1):
        while index + 1 < len(items) and starts[index + 1] <= found.offset:
            index += 1
        if found.kind == "wstr":
            first_wide.setdefault(items[index].oid, found.offset)

    best: tuple[BulkObject, list[tuple[str, int]]] | None = None
    for item in items:
        start = first_wide.get(item.oid)
        if start is None:
            continue
        entries = _walk_table(body, item, start)
        if entries and (best is None or len(entries) > len(best[1])):
            best = (item, entries)
    return best


def _read_record(body: bytes, item: BulkObject) -> dict | None:
    """Fields of one parameter record, or ``None`` if it does not read."""
    found = _wstr_at(body, item.offset + PREAMBLE, item.end)
    if found is None:
        return None
    expression, pos = found

    role = ""
    after = pos
    for padding in range(_MAX_PADDING + 1):
        candidate = _wstr_at(body, pos + padding, item.end)
        if candidate is not None and _is_label(candidate[0]):
            role, after = candidate
            break
    if not role:
        return None

    fields: list[str] = []
    for _ in range(3):
        candidate = _wstr_at(body, after, item.end)
        if candidate is None:
            return None
        fields.append(candidate[0])
        after = candidate[1]
    comment, unit, name = fields

    if after + 8 > item.end:
        return None
    value = struct.unpack_from("<d", body, after)[0]
    after += 8

    if after + 10 > item.end or body[after + 1] != _REFERENCE:
        return None
    manager = struct.unpack_from("<Q", body, after + 2)[0]

    revision = next(
        (
            found.value
            for found in scan_strings(body, start=after + 10, end=item.end, min_len=1)
            if found.kind == "str8" and found.value.isdigit()
        ),
        "",
    )
    return {
        "name": name,
        "number": struct.unpack_from("<I", body, item.offset + NUMBER_AT)[0],
        "role": role,
        "unit": unit,
        "expression": expression,
        "value": value,
        "comment": comment,
        "revision": revision,
        "manager": manager,
    }


def read_parameters(segment: Segment) -> Parameters:
    """Read a design segment's parameters, in creation order.

    A record is kept only when the name it carries is the name the table filed
    it under.  That check is what makes the bounded padding search above safe:
    a misread lands on the wrong string and is reported in
    :attr:`Parameters.unreadable` rather than published as a parameter.
    """
    found = find_table(segment)
    if found is None:
        return Parameters()
    table, entries = found

    body = segment.bulk.body
    by_id = {item.oid: item for item in segment.objects()}
    values: list[Parameter] = []
    unreadable: list[str] = []
    managers: set[int] = set()

    for name, oid in sorted(entries, key=lambda entry: entry[1]):
        item = by_id.get(oid)
        record = _read_record(body, item) if item is not None else None
        if record is None or record["name"] != name:
            unreadable.append(name)
            continue
        managers.add(record.pop("manager"))
        values.append(Parameter(oid=oid, **record))

    return Parameters(
        table=table.oid,
        # One manager per document in every sample; ambiguity would mean the
        # table gathered records that are not all one design's, so say nothing.
        manager=managers.pop() if len(managers) == 1 else 0,
        declared=len(entries),
        values=values,
        unreadable=tuple(unreadable),
    )
