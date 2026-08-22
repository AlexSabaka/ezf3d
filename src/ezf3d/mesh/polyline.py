"""Turn B-Rep edges into polylines.

Edge discretisation is the cheapest useful thing that can be done with the
geometry layer: it needs a curve and the edge's extent, and nothing else — no
surface inversion, no trimming, no seam handling.  That is why a wireframe can
be drawn for every body in a design, including the spline-heavy ones whose
faces will not tessellate until Phase 2.4.

Segment counts are driven by **chord tolerance**: the largest distance allowed
between the polyline and the true curve.  For a circle of radius *r* split into
steps of angle *d*, that sagitta is ``r * (1 - cos(d / 2))``, which inverts to
give the step directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ezf3d.asm.brep import Edge, Shape
from ezf3d.asm.geometry import Curve, Ellipse, GeometryError, SplineCurve, Straight

#: Default chord tolerance, in kernel units (cm).  0.1 mm is finer than any
#: display resolution these renders reach and coarse enough to stay quick.
DEFAULT_CHORD_TOLERANCE = 0.01

#: Never emit more than this many segments for one edge, however tight the
#: tolerance or however large the radius.
MAX_SEGMENTS = 512


@dataclass(slots=True)
class Wireframe:
    """Polylines for one body, plus what could not be drawn exactly."""

    #: Each entry is an ``(n, 3)`` array of points in kernel units (cm).
    polylines: list[np.ndarray] = field(default_factory=list)
    #: Edges drawn as a straight chord because their curve is not evaluable yet.
    approximated: int = 0
    #: Edges left out — degenerate, or on a curve ezf3d cannot evaluate.
    omitted: int = 0
    #: Edges skipped for want of a curve or vertices.
    skipped: int = 0

    @property
    def count(self) -> int:
        return len(self.polylines)

    def points(self) -> np.ndarray:
        """Every point, concatenated — handy for bounds."""
        if not self.polylines:
            return np.zeros((0, 3))
        return np.concatenate(self.polylines)

    def segments(self) -> np.ndarray:
        """All polylines flattened to an ``(n, 2, 3)`` array of line segments."""
        pieces = [
            np.stack([line[:-1], line[1:]], axis=1) for line in self.polylines if len(line) >= 2
        ]
        if not pieces:
            return np.zeros((0, 2, 3))
        return np.concatenate(pieces)

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        points = self.points()
        if not len(points):
            return None
        return points.min(axis=0), points.max(axis=0)


def _segment_count(curve: Curve, span: float, tolerance: float) -> int:
    """How many segments a parameter *span* needs to stay within *tolerance*."""
    if isinstance(curve, Straight):
        return 1
    if isinstance(curve, Ellipse):
        radius = max(curve.major_radius, curve.minor_radius)
        if radius <= 0.0:
            return 1
        ratio = 1.0 - min(tolerance / radius, 1.0)
        step = 2.0 * math.acos(ratio) if ratio > -1.0 else math.pi
        if step <= 0.0:
            return MAX_SEGMENTS
        return int(min(max(math.ceil(abs(span) / step), 1), MAX_SEGMENTS))
    return 1


def edge_range(edge: Edge) -> tuple[float, float] | None:
    """The edge's extent in its curve's parameterisation, direction included.

    Uses the stored range where it is real, and falls back to inverting at the
    vertices where it is a sentinel.  A closed edge — a full circular rim —
    spans a whole period, which inversion alone cannot tell apart from a
    zero-length arc, so the stored range is what resolves it.
    """
    curve = edge.curve
    if curve is None:
        return None
    t0, t1 = edge.range
    start, end = edge.parameter(t0), edge.parameter(t1)

    if not edge.range_is_sentinel():
        return start, end

    derived = edge.derived_range()
    if derived is None:
        return None
    a, b = derived
    if edge.is_closed and curve.period is not None:
        # Same vertex at both ends: the edge goes all the way round.  Keep the
        # direction the stored range implies.
        period = curve.period
        return (a, a + period) if end >= start else (a, a - period)
    if curve.is_periodic and curve.period is not None:
        # Take the arc that matches the stored range's direction.
        period = curve.period
        while b < a and end > start:
            b += period
        while b > a and end < start:
            b -= period
    return a, b


def discretise_edge(
    edge: Edge,
    tolerance: float = DEFAULT_CHORD_TOLERANCE,
    *,
    chords: bool = False,
) -> np.ndarray | None:
    """Sample *edge* into a polyline, or ``None`` if it cannot be drawn.

    A curve ezf3d cannot evaluate yet is omitted unless *chords* is set, in
    which case it is drawn as the straight line between its vertices.  Chords
    are off by default because a wrong line is worse than a missing one: on a
    spline-heavy design they cut straight across the part and read as structure
    that is not there.
    """
    if edge.is_degenerate:
        return None
    curve = edge.curve
    ends = edge.endpoints()
    if ends is None:
        return None
    if curve is None or isinstance(curve, SplineCurve):
        if not chords or edge.is_closed:
            return None
        return np.stack(ends)

    span = edge_range(edge)
    if span is None:
        return np.stack(ends)
    start, end = span
    try:
        count = _segment_count(curve, end - start, tolerance)
        points = curve.points_at(np.linspace(start, end, count + 1))
    except (GeometryError, ValueError):
        return np.stack(ends)

    # The vertices are authoritative; snap the ends onto them so adjacent
    # polylines meet exactly rather than within tolerance.
    points[0] = ends[0]
    points[-1] = ends[1]
    return points


def wireframe(
    shape: Shape,
    tolerance: float = DEFAULT_CHORD_TOLERANCE,
    *,
    chords: bool = False,
) -> Wireframe:
    """Discretise every edge reachable from a body."""
    result = Wireframe()
    for edge in shape.edges():
        curve = edge.curve
        unsupported = curve is None or isinstance(curve, SplineCurve)
        line = discretise_edge(edge, tolerance, chords=chords)
        if line is None:
            if unsupported and not edge.is_degenerate:
                result.omitted += 1
            else:
                result.skipped += 1
            continue
        if unsupported:
            result.approximated += 1
        result.polylines.append(line)
    return result
