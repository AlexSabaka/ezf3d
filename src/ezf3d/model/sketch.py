"""Sketches: the points and curves a design draws before it extrudes them.

Phase 3 could say that timeline entry #4 is an extrude that cuts 50 mm on one
side.  What it could not say is *what shape* -- the profile.  That was recorded
as unreachable because an extrude's ``inputs`` list never names its sketch, and
it does not: **0 of 88** extrudes across the samples reach one that way.

The reference simply runs the other way.  Each entity names its sketch::

    str8 EntityGenesis
    str8 IntrinsicMetaTypeuint64
    str8 pt_tag | crv_primary_id      what this record is
    str8 IntrinsicMetaTypeuint64
    ...
    f64 x, f64 y, f64 z               at +27 past the anchor, for a point
    ...
    0x01 u64 owner, 0x00 0x00         the sketch's geometry container
    str8 revision

Unlike a feature's operation code, these records **say what they are**: the
``str8`` key is on the wire.  There is no enum to guess.

**The owner reference is read, not inferred.**  It sits immediately before a
record's trailing revision string, and the sketch feature follows the container
it names by three ids in almost every case and four in the rest.  Over the four
samples it resolves **3,217 of 3,221** entities with no record reaching two
sketches, and it reaches *every* sketch each design has -- 8 of 8, 82 of 82 and
39 of 39.  Positional attribution was tried first, the way
:func:`~ezf3d.model.timeline.attribute` does it for parameters, and topped out
at 95%; this is better and it is a reading rather than a rule.

A record may nest a second ``EntityGenesis`` block -- a spline carrying its own
dimension does -- so every revision-shaped string is tried and the first whose
preceding reference lands on a sketch wins.  Anchoring on the last one instead
loses exactly those splines, which is the same trap the extrude settings
sprang in 3.6b.

**Coordinates are two-dimensional**, in the sketch's own frame: ``z`` is zero
for 1,876 of 1,912 points and never larger than 1.8e-15 in the rest, which is
rounding rather than depth.  The frame itself is *not* in the design stream --
see :doc:`unknowns </format/unknowns>`.

What checks the reading is the design's own parameters.  A sketch's ``Linear
Dimension-N`` must be the distance between two of that sketch's points, and it
is for **155 of 179** of them to within 1e-7 cm.  The parameter record and the
point records are different objects reached by different scans, so their
agreement is not self-consistency.  The 24 that miss are dimensions measuring a
point against a *line* rather than against another point, which this module
does not yet resolve.
"""

from __future__ import annotations

import math
import struct
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import combinations

from ezf3d.model.parameters import Parameter, read_parameters
from ezf3d.model.timeline import Timeline, read_timeline
from ezf3d.streams.primitives import scan_strings
from ezf3d.streams.segment import BulkObject, Segment


def _key(name: str) -> bytes:
    """A ``str8`` on the wire: a ``u32`` byte count and the ASCII."""
    return struct.pack("<I", len(name)) + name.encode("ascii")


#: The ``str8`` that says a record is a sketch point.
POINT_KEY = "pt_tag"

#: And a sketch curve.  ``crv_secondary_id`` follows it in the same record.
CURVE_KEY = "crv_primary_id"

#: The type word each key is followed by; the coordinates are measured from
#: the end of the one that follows the key, not from the record's start,
#: because the three record shapes in the samples begin differently.
_ANCHOR = "IntrinsicMetaTypeuint64"

#: Bytes between that anchor and the ``f64`` triple.  One offset for all three
#: point shapes -- ``(169, 221, 225)`` bytes over four subsystem revisions --
#: which is what says it is a field rather than an alignment.
COORDINATE_GAP = 27

#: A reference: ``0x01``, a ``u64`` object id, two spare bytes.
_REFERENCE = 0x01
_STRIDE = 11

#: Most ids between the container an entity names and the sketch feature
#: itself.  Three for all but 22 of the entities resolved across the samples,
#: four for those; the margin is deliberate slack, and a wider one would start
#: matching the *next* sketch, so it is bounded rather than open.
OWNER_REACH = 8

#: Record sizes considered.  A point is 169 to 225 bytes and a curve 295 to
#: 815; the bounds keep the scan off the megabyte-scale objects without
#: deciding anything about what is inside them.
_MIN_RECORD = 40
_MAX_RECORD = 2000

#: How close a dimension has to come to a point-pair distance to count as
#: re-derived.  Coordinates are stored to full double precision, so this is
#: far tighter than any modelling tolerance.
DIMENSION_TOLERANCE = 1e-7

_POINT_MARK = _key(POINT_KEY)
_CURVE_MARK = _key(CURVE_KEY)
_ANCHOR_MARK = _key(_ANCHOR)


@dataclass(frozen=True, slots=True)
class Point:
    """A sketch point, in the sketch's own two-dimensional frame."""

    oid: int
    #: Centimetres, Fusion's internal unit.
    x: float
    y: float

    def distance(self, other: Point) -> float:
        return math.dist((self.x, self.y), (other.x, other.y))


@dataclass(frozen=True, slots=True)
class Curve:
    """A sketch curve, located but not yet typed.

    The record's size separates line from arc from circle *within* one
    document -- 356, 367 and 347 bytes in SUCKER, corroborated by which
    sketches carry a ``Diameter Dimension`` -- but 3.6b established that record
    size classifies nothing *across* documents, and these sizes do change with
    the subsystem revision.  So the size is carried as the lead it is, and the
    type is left to a later phase rather than guessed at here.
    """

    oid: int
    #: Bytes in the record.
    size: int


@dataclass(frozen=True, slots=True)
class Sketch:
    """One sketch, its geometry, and the dimensions that drive it."""

    oid: int
    #: Position in the timeline, or ``-1`` for a sketch the list does not hold.
    index: int
    #: The label Fusion shows, or the name the user gave it.
    name: str
    points: tuple[Point, ...] = ()
    curves: tuple[Curve, ...] = ()
    #: The sketch's own dimensions, as attributed by
    #: :func:`~ezf3d.model.timeline.attribute`.
    parameters: tuple[Parameter, ...] = ()

    @property
    def in_timeline(self) -> bool:
        return self.index >= 0

    def extent(self) -> tuple[float, float, float, float]:
        """``(xmin, ymin, xmax, ymax)`` in centimetres; zeros when empty."""
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [point.x for point in self.points]
        ys = [point.y for point in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    def distances(self) -> set[float]:
        """Every distance between two of this sketch's points."""
        return {a.distance(b) for a, b in combinations(self.points, 2)}

    def dimension_check(self) -> tuple[int, tuple[str, ...]]:
        """``(linear dimensions re-derived, those that were not)``.

        The cross-check this module rests on: a ``Linear Dimension-N`` the
        parameter table holds must be a distance between two points the
        geometry records hold.  A miss is reported rather than tolerated --
        a dimension can legitimately measure a point against a line, which
        this does not resolve, so misses are expected and named.
        """
        spans = self.distances()
        hit = 0
        missed: list[str] = []
        for parameter in self.parameters:
            if not parameter.role.startswith("Linear Dimension"):
                continue
            if any(abs(span - abs(parameter.value)) < DIMENSION_TOLERANCE for span in spans):
                hit += 1
            else:
                missed.append(parameter.role)
        return hit, tuple(missed)


@dataclass(slots=True)
class Sketches:
    """Every sketch a design has, and what the scan could not place."""

    sketches: list[Sketch] = field(default_factory=list)
    #: Entity records whose owner reference reached no sketch.  Counted rather
    #: than dropped: 4 across the four samples, all of them curves.
    unowned: int = 0

    def __len__(self) -> int:
        return len(self.sketches)

    def __iter__(self):
        return iter(self.sketches)

    def by_id(self) -> dict[int, Sketch]:
        return {sketch.oid: sketch for sketch in self.sketches}

    def points(self) -> int:
        return sum(len(sketch.points) for sketch in self.sketches)

    def curves(self) -> int:
        return sum(len(sketch.curves) for sketch in self.sketches)

    def check(self) -> tuple[int, tuple[tuple[int, str], ...]]:
        """``(dimensions re-derived, (sketch id, role) for those that were not)``."""
        hit = 0
        missed: list[tuple[int, str]] = []
        for sketch in self.sketches:
            found, gaps = sketch.dimension_check()
            hit += found
            missed.extend((sketch.oid, role) for role in gaps)
        return hit, tuple(missed)


def read_point(body: bytes, item: BulkObject) -> tuple[float, float] | None:
    """The ``(x, y)`` of a point record, or ``None`` if it is not one."""
    at = _coordinates(body, item, _POINT_MARK)
    if at is None:
        return None
    x, y, _z = struct.unpack_from("<3d", body, at)
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return x, y


def _coordinates(body: bytes, item: BulkObject, mark: bytes) -> int | None:
    """Offset of the ``f64`` triple, measured from the key's type word."""
    key = body.find(mark, item.offset, item.end)
    if key < 0:
        return None
    anchor = body.find(_ANCHOR_MARK, key + len(mark), item.end)
    if anchor < 0:
        return None
    at = anchor + len(_ANCHOR_MARK) + COORDINATE_GAP
    return at if at + 24 <= item.end else None


def owner_of(body: bytes, item: BulkObject, sketches: list[int]) -> int | None:
    """The sketch an entity record names, or ``None``.

    Every revision-shaped string in the record is tried and the reference
    before it read; the first that lands within :data:`OWNER_REACH` of a
    sketch wins.  Trying only the last one loses the splines, which nest a
    second ``EntityGenesis`` block of their own.
    """
    if not sketches:
        return None
    for found in scan_strings(body, start=item.offset, end=item.end, min_len=3):
        if found.kind != "str8" or not found.value.isdigit():
            continue
        at = found.offset - _STRIDE
        if at < item.offset or body[at] != _REFERENCE:
            continue
        (container,) = struct.unpack_from("<Q", body, at + 1)
        position = bisect_left(sketches, container)
        if position < len(sketches) and sketches[position] - container <= OWNER_REACH:
            return sketches[position]
    return None


def _count_entities(segment: Segment) -> int:
    """Point and curve records in a segment, without attributing any."""
    body = segment.bulk.body
    total = 0
    for item in segment.objects():
        size = item.end - item.offset
        if not (_MIN_RECORD < size < _MAX_RECORD):
            continue
        if (
            body.find(_POINT_MARK, item.offset, item.end) >= 0
            or body.find(_CURVE_MARK, item.offset, item.end) >= 0
        ):
            total += 1
    return total


def read_sketches(
    segment: Segment,
    timeline: Timeline | None = None,
    parameters: Iterable[Parameter] | None = None,
) -> Sketches:
    """Read a design segment's sketches, their points and their curves.

    *timeline* supplies which objects are sketches and what they are called;
    one is read if not given.  It must be the **wider** set -- every named
    feature-shaped object, which :attr:`~ezf3d.model.timeline.Timeline.named`
    carries -- and not only the entries the list holds: Focuser Mk1 keeps 6 of
    its 39 sketches outside the timeline, and their geometry is in the stream
    either way.
    """
    if timeline is None:
        timeline = read_timeline(segment, parameters or read_parameters(segment).values)
    body = segment.bulk.body

    order = {feature.oid: feature for feature in timeline if feature.kind == "Sketch"}
    ids = sorted(oid for oid, name in timeline.named.items() if name == "Sketch")
    ids = sorted(set(ids) | set(order))
    if not ids:
        # A design can hold entity records and no sketch to hang them on: the
        # registry-less `.f3z` members have no feature objects at all, and
        # Roundified Cray keeps 51 such records.  Counted, so that "no
        # sketches" does not read as "no sketch geometry".
        return Sketches(unowned=_count_entities(segment))

    points: dict[int, list[Point]] = {oid: [] for oid in ids}
    curves: dict[int, list[Curve]] = {oid: [] for oid in ids}
    unowned = 0
    for item in segment.objects():
        size = item.end - item.offset
        if not (_MIN_RECORD < size < _MAX_RECORD):
            continue
        is_point = body.find(_POINT_MARK, item.offset, item.end) >= 0
        if not is_point and body.find(_CURVE_MARK, item.offset, item.end) < 0:
            continue
        owner = owner_of(body, item, ids)
        if owner is None:
            unowned += 1
            continue
        if is_point:
            found = read_point(body, item)
            if found is None:
                unowned += 1
                continue
            points[owner].append(Point(oid=item.oid, x=found[0], y=found[1]))
        else:
            curves[owner].append(Curve(oid=item.oid, size=size))

    return Sketches(
        sketches=[
            Sketch(
                oid=oid,
                index=order[oid].index if oid in order else -1,
                name=order[oid].name if oid in order else "Sketch",
                points=tuple(points[oid]),
                curves=tuple(curves[oid]),
                parameters=order[oid].parameters if oid in order else (),
            )
            for oid in ids
        ],
        unowned=unowned,
    )
