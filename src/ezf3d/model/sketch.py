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
from collections import Counter
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

#: The second key in a curve record; the endpoint block is measured from the
#: type word that follows *it*.
_CURVE_TAIL = "crv_secondary_id"

#: Bytes between that type word and the block of point references.  104 in all
#: four documents, where the block's absolute offset is 266 in three of them
#: and 214 in Focuser Mk1 -- which is what makes it a field and not an offset
#: that happens to work.
ENDPOINT_GAP = 104

#: Offsets past the same anchor for the two numbers a curve carries.  The
#: radius is checkable: for an arc it must equal the distance from the centre
#: it names to the endpoint it names, and it does for all 282 to 2.2e-07 cm.
RADIUS_AT = 80
SPAN_AT = ENDPOINT_GAP - 8

#: How many points a curve references, and therefore what it is.  Not a size
#: rule: 3.6b established that record size classifies nothing across
#: documents, and these sizes do move with the revision.  The count does not.
CURVE_KINDS = {1: "Circle", 2: "Line", 3: "Arc"}

#: A circle's span is a full turn.  Pinned because it is what separates a
#: one-reference circle from a misread record.
FULL_TURN = 2.0 * math.pi

#: Agreement asked of a curve's stored radius and span against the geometry
#: its own points describe -- centimetres and radians.
CURVE_TOLERANCE = 1e-6

_POINT_MARK = _key(POINT_KEY)
_CURVE_MARK = _key(CURVE_KEY)
_CURVE_TAIL_MARK = _key(_CURVE_TAIL)
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
    """A sketch curve: what kind it is, and which of its sketch's points it uses.

    The kind is **how many points the curve references**, not how large its
    record is.  Size does separate the kinds inside one document -- 347, 356
    and 367 bytes in SUCKER -- but 3.6b established that record size classifies
    nothing across documents, and these sizes move with the revision.  The
    reference count does not: 1, 2 or 3 in every document, and every reference
    is a point of the curve's own sketch, for all 1,334 curves in the samples.
    """

    oid: int
    #: Bytes in the record, carried for the record it is rather than used.
    size: int
    #: ``Circle``, ``Line`` or ``Arc``; ``""`` when the block did not read.
    kind: str = ""
    #: The points it names.  A line's two endpoints; an arc's **centre first**,
    #: then its two endpoints; a circle's centre alone.
    points: tuple[int, ...] = ()
    #: Centimetres.  Zero for a line, which has none.
    radius: float = 0.0
    #: The parameter range the record stores: a full turn for a circle, the
    #: angle its endpoints subtend for an arc, and +/-1 for a line -- the sign
    #: is carried rather than interpreted, as the extrude's third code is.
    span: float = 0.0

    @property
    def centre(self) -> int | None:
        """The point a circle or arc turns about, or ``None`` for a line."""
        return self.points[0] if self.kind in ("Circle", "Arc") else None

    @property
    def ends(self) -> tuple[int, int] | None:
        """The two points a line or arc runs between; ``None`` for a circle."""
        if self.kind == "Line":
            return (self.points[0], self.points[1])
        if self.kind == "Arc":
            return (self.points[1], self.points[2])
        return None

    @property
    def is_closed(self) -> bool:
        return self.kind == "Circle"


@dataclass(frozen=True, slots=True)
class Loop:
    """A closed chain of curves -- the thing an extrude sweeps.

    A sketch is a *graph*, not a single outline: construction lines and open
    chains are ordinary, and 245 of the samples' 1,255 curve endpoints have
    only one curve on them.  So the loops a sketch yields are the components
    every one of whose points carries exactly two curves, plus each circle,
    which is a loop by itself.  What is left over is reported rather than
    forced into a shape it does not have.
    """

    #: Curve ids, in the order they chain.
    curves: tuple[int, ...]
    #: Point ids, in the same order; empty for a circle.
    points: tuple[int, ...] = ()

    def __len__(self) -> int:
        return len(self.curves)


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

    def kinds(self) -> Counter[str]:
        """How many circles, lines and arcs."""
        return Counter(curve.kind for curve in self.curves if curve.kind)

    def loops(self) -> tuple[Loop, ...]:
        """The closed chains this sketch's curves form.

        Each circle is a loop of its own.  The rest are walked as a graph on
        point ids, and a component counts only when every one of its points
        carries exactly two curves -- anything else is an open chain or a
        junction, which :meth:`loose` counts.
        """
        found = [Loop(curves=(c.oid,)) for c in self.curves if c.kind == "Circle"]
        adjacency: dict[int, list[tuple[int, int]]] = {}
        for curve in self.curves:
            ends = curve.ends
            if ends is None:
                continue
            adjacency.setdefault(ends[0], []).append((curve.oid, ends[1]))
            adjacency.setdefault(ends[1], []).append((curve.oid, ends[0]))

        seen: set[int] = set()
        for start in adjacency:
            if start in seen:
                continue
            component = _component(adjacency, start)
            seen |= component
            if any(len(adjacency[point]) != 2 for point in component):
                continue
            found.append(_walk(adjacency, start))
        return tuple(found)

    def loose(self) -> int:
        """Curves that no closed loop uses -- open chains and junctions."""
        used = {oid for loop in self.loops() for oid in loop.curves}
        return sum(1 for curve in self.curves if curve.oid not in used)

    def curve_check(self) -> tuple[int, tuple[int, ...]]:
        """``(curves whose numbers match their own geometry, those that do not)``.

        The check that says the kinds are read rather than guessed.  An arc
        names a centre and two endpoints, so its stored radius must be the
        distance from that centre to those endpoints and its stored span the
        angle they subtend -- neither of which the record's *size* could
        predict.  A circle must store a full turn.  Lines carry no geometry to
        check, and are not counted either way.
        """
        at = {point.oid: point for point in self.points}
        good = 0
        bad: list[int] = []
        for curve in self.curves:
            if curve.kind == "Circle":
                ok = abs(curve.span - FULL_TURN) < CURVE_TOLERANCE and curve.radius > 0.0
            elif curve.kind == "Arc":
                ok = _arc_agrees(curve, at)
            else:
                continue
            good += ok
            if not ok:
                bad.append(curve.oid)
        return good, tuple(bad)

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

    def kinds(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for sketch in self.sketches:
            total += sketch.kinds()
        return total

    def loops(self) -> int:
        return sum(len(sketch.loops()) for sketch in self.sketches)

    def curve_check(self) -> tuple[int, tuple[int, ...]]:
        """``(curves agreeing with their own geometry, those that do not)``."""
        good = 0
        bad: list[int] = []
        for sketch in self.sketches:
            found, gaps = sketch.curve_check()
            good += found
            bad.extend(gaps)
        return good, tuple(bad)

    def check(self) -> tuple[int, tuple[tuple[int, str], ...]]:
        """``(dimensions re-derived, (sketch id, role) for those that were not)``."""
        hit = 0
        missed: list[tuple[int, str]] = []
        for sketch in self.sketches:
            found, gaps = sketch.dimension_check()
            hit += found
            missed.extend((sketch.oid, role) for role in gaps)
        return hit, tuple(missed)


def _component(adjacency: dict[int, list[tuple[int, int]]], start: int) -> set[int]:
    """Every point reachable from *start* along curves."""
    stack, seen = [start], set()
    while stack:
        point = stack.pop()
        if point in seen:
            continue
        seen.add(point)
        stack.extend(other for _, other in adjacency[point] if other not in seen)
    return seen


def _walk(adjacency: dict[int, list[tuple[int, int]]], start: int) -> Loop:
    """Order a component's curves by following it round from *start*."""
    curves: list[int] = []
    points: list[int] = [start]
    previous, point = None, start
    while True:
        step = next(
            (edge for edge in adjacency[point] if edge[0] != previous),
            adjacency[point][0],
        )
        previous, point = step
        curves.append(previous)
        if point == start:
            break
        points.append(point)
        if len(curves) > len(adjacency):
            break
    return Loop(curves=tuple(curves), points=tuple(points))


def _arc_agrees(curve: Curve, at: dict[int, Point]) -> bool:
    """An arc's stored radius and span against the points it names."""
    if len(curve.points) != 3 or not all(oid in at for oid in curve.points):
        return False
    centre, first, last = (at[oid] for oid in curve.points)
    if abs(centre.distance(first) - abs(curve.radius)) > CURVE_TOLERANCE:
        return False
    start = math.atan2(first.y - centre.y, first.x - centre.x)
    end = math.atan2(last.y - centre.y, last.x - centre.x)
    turn = (end - start) % FULL_TURN
    return min(abs(turn - abs(curve.span)), abs(FULL_TURN - turn - abs(curve.span))) < (
        CURVE_TOLERANCE
    )


def read_curve(body: bytes, item: BulkObject, points: set[int]) -> Curve | None:
    """Kind, points, radius and span of a curve record.

    The endpoint block is found from the type word after ``crv_secondary_id``
    rather than from the record's start, and every reference in it has to be a
    point of the sketch being read -- which is what stops the walk running on
    into whatever follows.
    """
    size = item.end - item.offset
    tail = body.find(_CURVE_TAIL_MARK, item.offset, item.end)
    if tail < 0:
        return Curve(oid=item.oid, size=size)
    anchor = body.find(_ANCHOR_MARK, tail, item.end)
    if anchor < 0:
        return Curve(oid=item.oid, size=size)
    base = anchor + len(_ANCHOR_MARK)
    at = base + ENDPOINT_GAP
    if at + _STRIDE > item.end or base + ENDPOINT_GAP > item.end:
        return Curve(oid=item.oid, size=size)

    named: list[int] = []
    while at + _STRIDE <= item.end and body[at] == _REFERENCE:
        (oid,) = struct.unpack_from("<Q", body, at + 1)
        if oid not in points:
            break
        named.append(oid)
        at += _STRIDE
    kind = CURVE_KINDS.get(len(named), "")
    if not kind:
        return Curve(oid=item.oid, size=size)
    (radius,) = struct.unpack_from("<d", body, base + RADIUS_AT)
    (span,) = struct.unpack_from("<d", body, base + SPAN_AT)
    return Curve(
        oid=item.oid,
        size=size,
        kind=kind,
        points=tuple(named),
        radius=0.0 if kind == "Line" else radius,
        span=span,
    )


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
    pending: dict[int, list[BulkObject]] = {oid: [] for oid in ids}
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
            pending[owner].append(item)

    # Curves are read second because a curve's references are validated
    # against its own sketch's points, which is what bounds the walk.
    curves: dict[int, list[Curve]] = {}
    for oid in ids:
        owned = {point.oid for point in points[oid]}
        curves[oid] = [
            read_curve(body, item, owned) or Curve(oid=item.oid, size=0) for item in pending[oid]
        ]

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
