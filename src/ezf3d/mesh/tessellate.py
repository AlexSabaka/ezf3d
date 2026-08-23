"""Turn trimmed B-Rep faces into triangles.

A face is a region of a surface bounded by loops of edges.  Triangulating one
means taking those loops into the surface's own parameter space, filling the
region there, and lifting the result back.

Two properties are worth stating because they drive the design:

**No Steiner points.**  Every mesh vertex comes from an edge polyline, and the
two faces sharing an edge discretise that same edge identically, so their
triangles meet exactly rather than within tolerance.  That is what makes the
result watertight; adding interior points would need a constrained
triangulation to stay conformal, and cracks would follow if it were not.

**Periodic faces are a separate case.**  A cylinder wall has two loops that each
wrap right around, and in UV they are two open lines, not a closed polygon —
ear clipping cannot see it.  Those are stitched into a strip instead.

Faces that cannot be built are counted and named, never dropped quietly.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np

from ezf3d.asm.brep import Face, Loop, Shape
from ezf3d.asm.geometry import GeometryError, Plane, SplineSurface, Surface
from ezf3d.mesh.mesh import Mesh
from ezf3d.mesh.polyline import DEFAULT_CHORD_TOLERANCE, discretise_edge, usable_curve

#: How close a loop's total u-span must be to a full period to count as wrapping.
_WRAP_TOLERANCE = 0.2

#: Tessellate faces that sit on a spline surface.  Off, because the
#: approximation ezf3d finds for a procedural surface has not been shown to be
#: the right one: even sampled at 80x80 it sits a quarter of a millimetre from
#: the face's own vertices, and triangulating it produced worse geometry than
#: leaving the face out. Spline *curves* are verified and are always used.
TESSELLATE_SPLINE_SURFACES = False

#: How far a face's vertices may sit from its approximating spline surface
#: before the approximation is treated as the wrong one.
SURFACE_FIT_TOLERANCE = 5e-3

#: Cap on rows added across a strip, however tight the tolerance.
_MAX_ROWS = 64

#: How many times a measured strip may be halved before giving up.
_MAX_REFINE_STEPS = 5

#: Floor on how far a loop point may sit from the face's surface, so that a
#: very tight chord tolerance does not start rejecting the kernel's own
#: rounding.  An order of magnitude above the 1.2e-05 cm that 99.9 % of loops
#: come within.
_ON_SURFACE = 1e-4

#: Points sampled per ring when checking it against the surface.  A loop from
#: another face is centimetres away, not microns; a handful finds it.
_RING_SAMPLE = 8

#: A face whose triangles stray this far past the tolerance is reported rather
#: than meshed.  Marginal overshoot is fine; a fan across a curved face is not.
_REJECT_FACTOR = 4.0


@dataclass(slots=True)
class Tessellation:
    """A mesh plus an honest account of what did not make it in."""

    mesh: Mesh = field(default_factory=Mesh)
    faces_meshed: int = 0
    #: Why each unbuilt face was left out, by reason.
    unsupported: Counter[str] = field(default_factory=Counter)
    #: Largest distance from a triangle's centroid to its own surface.
    max_deviation: float = 0.0
    #: Faces whose triangles stray beyond the tolerance asked for.  Small and
    #: measured rather than hidden: notched regions on a curved surface are
    #: not monotone in either parameter and fall back to a fan.
    faces_over_tolerance: int = 0
    #: Distinct solids found, and how many came out closed and manifold.
    solids: int = 0
    watertight_solids: int = 0
    #: Solids whose B-Rep is closed and every face of which was meshed — the
    #: population for which watertightness is a fair thing to ask.
    closed_candidates: int = 0

    @property
    def faces_skipped(self) -> int:
        return int(sum(self.unsupported.values()))

    @property
    def is_complete(self) -> bool:
        return self.faces_skipped == 0


def _loop_polyline(loop: Loop, tolerance: float) -> np.ndarray | None:
    """A loop's boundary as one closed 3D polyline, or ``None`` if incomplete.

    Points come straight from the shared edge polylines so that the two faces
    meeting at an edge produce identical vertices.
    """
    pieces: list[np.ndarray] = []
    for coedge in loop.coedges():
        edge = coedge.edge
        if edge is None:
            return None
        if edge.is_degenerate:
            continue
        if usable_curve(edge) is None:
            return None
        line = discretise_edge(edge, tolerance)
        if line is None or len(line) < 2:
            return None
        if not coedge.sense:
            line = line[::-1]
        pieces.append(line[:-1])
    if not pieces:
        return None
    return np.concatenate(pieces)


def _rings_lie_on(surface: Surface, rings: list[np.ndarray], tolerance: float) -> bool:
    """Whether every ring is close enough to *surface* to be its boundary.

    A face's loops are reached by walking a ``next`` chain, and in a design
    saved with rollback history that chain can run into a loop belonging to a
    different face — the loop's own back-pointer is no help, since two faces
    can reach one loop record and the one it names is not always the one whose
    surface the points lie on.

    Geometry settles it, and cleanly: over 25,803 loops in the samples the
    99.9th percentile distance to the face's own surface is 1.2e-05 cm and
    only three exceed a thousandth, one of them by 2.9 cm.  That last one gave
    a plane at *x* = -0.3 a second outline at *x* = 2.6, which triangulated
    into 29.1 cm2 where the face encloses 0.8.
    """
    bar = max(tolerance, _ON_SURFACE)
    for ring in rings:
        step = max(1, len(ring) // _RING_SAMPLE)
        try:
            for point in ring[::step]:
                if abs(surface.distance_to(point)) > bar:
                    return False
        except (GeometryError, ValueError, ZeroDivisionError):
            return False
    return True


def _to_uv(surface: Surface, points: np.ndarray) -> np.ndarray:
    uv = np.array([surface.invert(point) for point in points], dtype=float)
    for axis, period in ((0, surface.u_period), (1, surface.v_period)):
        if period is None:
            continue
        # Unwrap so a boundary crossing the seam reads as a continuous run
        # rather than jumping a whole period.
        column = uv[:, axis]
        steps = np.diff(column)
        steps -= np.round(steps / period) * period
        uv[:, axis] = np.concatenate([[column[0]], column[0] + np.cumsum(steps)])
    return uv


def _signed_area(uv: np.ndarray) -> float:
    x, y = uv[:, 0], uv[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _wraps(uv: np.ndarray, period: float | None) -> bool:
    """Does this ring go right around the surface?

    Measured as total winding, closing step included.  Comparing the raw
    min-to-max span instead reads one sampling step short of a full turn, which
    on a 31-point circle is 0.2 radians — enough to mistake every cylinder wall
    for an open patch and fan across it.
    """
    if period is None:
        return False
    u = uv[:, 0]
    steps = np.diff(np.concatenate([u, u[:1]]))
    steps -= np.round(steps / period) * period
    return abs(abs(float(steps.sum())) - period) < _WRAP_TOLERANCE


# -- ear clipping ----------------------------------------------------------


def _is_convex(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_triangle(p, a, b, c) -> bool:
    d1 = _is_convex(p, a, b)
    d2 = _is_convex(p, b, c)
    d3 = _is_convex(p, c, a)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def ear_clip(uv: np.ndarray) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon given counter-clockwise in UV."""
    count = len(uv)
    if count < 3:
        return []
    indices = list(range(count))
    triangles: list[tuple[int, int, int]] = []
    guard = 0
    limit = 2 * count * count + 16

    while len(indices) > 3 and guard < limit:
        guard += 1
        clipped = False
        for position in range(len(indices)):
            prev = indices[position - 1]
            here = indices[position]
            nxt = indices[(position + 1) % len(indices)]
            a, b, c = uv[prev], uv[here], uv[nxt]
            if _is_convex(a, b, c) <= 0:
                continue
            if any(
                _point_in_triangle(uv[other], a, b, c)
                for other in indices
                if other not in (prev, here, nxt)
            ):
                continue
            triangles.append((prev, here, nxt))
            indices.pop(position)
            clipped = True
            break
        if not clipped:
            # A self-intersecting or degenerate outline: fall back to a fan so
            # the face still contributes something rather than vanishing.
            break

    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))
    elif len(indices) > 3:
        for k in range(1, len(indices) - 1):
            triangles.append((indices[0], indices[k], indices[k + 1]))
    return triangles


def _bridge_holes(outer: np.ndarray, holes: list[np.ndarray]) -> tuple[np.ndarray, list[int]]:
    """Splice each hole into the outer outline, returning one simple polygon.

    Each hole is entered at its rightmost vertex and left again at the nearest
    outer vertex to its right, which is the standard cut that keeps the result
    simple.
    """
    polygon = list(outer)
    origin = list(range(len(outer)))
    base = len(outer)
    offsets = []
    for hole in holes:
        offsets.append(base)
        base += len(hole)

    for hole, offset in sorted(
        zip(holes, offsets, strict=True), key=lambda item: -item[0][:, 0].max()
    ):
        entry = int(np.argmax(hole[:, 0]))
        current = np.array(polygon)
        # Nearest outer vertex that lies to the right of the hole's entry.
        to_right = current[:, 0] >= hole[entry, 0]
        candidates = np.flatnonzero(to_right)
        if not len(candidates):
            candidates = np.arange(len(current))
        distances = np.linalg.norm(current[candidates] - hole[entry], axis=1)
        bridge = int(candidates[int(np.argmin(distances))])

        ring = [(entry + step) % len(hole) for step in range(len(hole) + 1)]
        inserted = [hole[i] for i in ring] + [polygon[bridge]]
        inserted_origin = [offset + i for i in ring] + [origin[bridge]]
        polygon = polygon[: bridge + 1] + inserted + polygon[bridge + 1 :]
        origin = origin[: bridge + 1] + inserted_origin + origin[bridge + 1 :]
    return np.array(polygon), origin


def _monotone_chains(uv: np.ndarray) -> tuple[list[int], list[int]] | None:
    """Split a ring into two chains running from its lowest *u* to its highest.

    Returns ``None`` unless both chains are monotone in *u*, which is what a
    band-shaped face — a cylinder wall, a fillet, a cone frustum — looks like
    once its boundary is in parameter space.
    """
    count = len(uv)
    if count < 3:
        return None
    u = uv[:, 0]
    start, end = int(np.argmin(u)), int(np.argmax(u))
    if start == end:
        return None

    forward = []
    index = start
    while True:
        forward.append(index)
        if index == end:
            break
        index = (index + 1) % count
        if len(forward) > count:
            return None

    backward = []
    index = start
    while True:
        backward.append(index)
        if index == end:
            break
        index = (index - 1) % count
        if len(backward) > count:
            return None

    for chain in (forward, backward):
        steps = np.diff(u[chain])
        if len(steps) and steps.min() < -1e-12:
            return None
    return forward, backward


def _stitch_chains(
    uv: np.ndarray, first: list[int], second: list[int]
) -> list[tuple[int, int, int]]:
    """Triangulate a *u*-monotone polygon by walking both chains together.

    Advancing whichever chain is behind in *u* keeps every triangle inside one
    step of the boundary sampling, so a triangle never cuts across the surface
    the way a fan does.
    """
    u = uv[:, 0]
    triangles: list[tuple[int, int, int]] = []
    i = j = 0
    while i < len(first) - 1 or j < len(second) - 1:
        advance_first = j >= len(second) - 1 or (
            i < len(first) - 1 and u[first[i + 1]] <= u[second[j + 1]]
        )
        if advance_first:
            triangles.append((first[i], second[j], first[i + 1]))
            i += 1
        else:
            triangles.append((first[i], second[j], second[j + 1]))
            j += 1
    return [t for t in triangles if len({*t}) == 3]


def _v_subdivisions(surface: Surface, uv: np.ndarray, tolerance: float) -> int:
    """How many rows a strip needs across *v* to stay within *tolerance*."""
    radius = surface.v_radius
    if radius is None or radius <= 0.0:
        return 2 if isinstance(surface, SplineSurface) else 1
    span = float(uv[:, 1].max() - uv[:, 1].min())
    if span <= 0.0:
        return 1
    ratio = 1.0 - min(tolerance / radius, 1.0)
    step = 2.0 * math.acos(ratio) if ratio > -1.0 else math.pi
    if step <= 0.0:
        return _MAX_ROWS
    return int(min(max(math.ceil(span / step), 1), _MAX_ROWS))


def _subdivide_strip(
    surface: Surface,
    uv: np.ndarray,
    vertices: np.ndarray,
    triangles: list[tuple[int, int, int]],
    boundary: set[tuple[int, int]],
    rows: int,
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Split each strip triangle across *v*, leaving boundary edges alone.

    New points are interior to the face, so they cannot crack a shared edge —
    and each is placed by evaluating the surface, so the refined strip follows
    the real geometry rather than the chord.  Split points are keyed by the
    edge they sit on, which keeps neighbouring triangles conformal.
    """
    points = list(vertices)
    params = list(uv)
    periods = (surface.u_period, surface.v_period)
    cache: dict[tuple[int, int, int], int] = {}

    def between(low: np.ndarray, high: np.ndarray, blend: float) -> np.ndarray:
        """Interpolate two parameter pairs the short way round.

        Loops on a periodic surface are unwrapped independently, so two points
        one step apart on the ring can carry *u* values a couple of turns
        apart.  Interpolating those raw numbers sweeps right around the surface
        instead of crossing the strip.
        """
        target = np.array(high, dtype=float)
        for axis, period in enumerate(periods):
            if period is None:
                continue
            delta = target[axis] - low[axis]
            target[axis] -= round(delta / period) * period
        return low * (1.0 - blend) + target * blend

    def split_point(a: int, b: int, step: int) -> int:
        key = (min(a, b), max(a, b), step if a <= b else rows - step)
        if key in cache:
            return cache[key]
        t = step / rows
        low, high = (a, b) if a <= b else (b, a)
        blend = t if a <= b else 1.0 - t
        target = between(params[low], params[high], blend)
        points.append(surface.point_at(float(target[0]), float(target[1])))
        params.append(target)
        cache[key] = len(points) - 1
        return cache[key]

    out: list[tuple[int, int, int]] = []
    for tri in triangles:
        pair = next(
            (
                (x, y, z)
                for x, y, z in (
                    (tri[0], tri[1], tri[2]),
                    (tri[1], tri[2], tri[0]),
                    (tri[2], tri[0], tri[1]),
                )
                if (min(x, y), max(x, y)) in boundary
            ),
            None,
        )
        if pair is None:
            out.append(tri)
            continue
        a, b, apex = pair
        previous = (a, b)
        for step in range(1, rows + 1):
            if step == rows:
                out.append((previous[0], previous[1], apex))
                break
            left = split_point(a, apex, step)
            right = split_point(b, apex, step)
            out.append((previous[0], previous[1], right))
            out.append((previous[0], right, left))
            previous = (left, right)
    return np.array(points), out, np.array(params)


# -- faces -----------------------------------------------------------------


def _stitch_ring(
    lower: np.ndarray, upper: np.ndarray, lower_uv: np.ndarray, upper_uv: np.ndarray
) -> tuple[list[tuple[int, int, int]], set[tuple[int, int]]]:
    """Triangulate between two loops that each wrap right around.

    Walks both rings in *u*, always advancing whichever is behind, so only
    existing vertices are used and the strip stays conformal with its
    neighbours.  Returns the triangles and the boundary edges, which must be
    reported rather than recomputed: the walk visits vertices in sorted-*u*
    order, which is not the order the ring was built in.
    """
    n, m = len(lower), len(upper)
    if n < 2 or m < 2:
        return [], set()
    a_order = np.argsort(lower_uv[:, 0] % (2 * math.pi))
    b_order = np.argsort(upper_uv[:, 0] % (2 * math.pi))
    a_u = np.sort(lower_uv[:, 0] % (2 * math.pi))
    b_u = np.sort(upper_uv[:, 0] % (2 * math.pi))

    triangles: list[tuple[int, int, int]] = []
    boundary: set[tuple[int, int]] = set()
    for k in range(n):
        a, b = int(a_order[k]), int(a_order[(k + 1) % n])
        boundary.add((min(a, b), max(a, b)))
    for k in range(m):
        a, b = n + int(b_order[k]), n + int(b_order[(k + 1) % m])
        boundary.add((min(a, b), max(a, b)))

    i = j = 0
    while i < n or j < m:
        ai, bj = i % n, j % m
        take_lower = j >= m or (i < n and a_u[ai] <= b_u[bj])
        if take_lower:
            triangles.append((int(a_order[ai]), n + int(b_order[bj]), int(a_order[(i + 1) % n])))
            i += 1
        else:
            triangles.append(
                (int(a_order[ai]), n + int(b_order[bj]), n + int(b_order[(j + 1) % m]))
            )
            j += 1
    return [t for t in triangles if len({*t}) == 3], boundary


def _deviation(surface: Surface, params: np.ndarray, vertices: np.ndarray, triangles) -> float:
    """How far each triangle's middle sits from the surface beneath it.

    Measured through the parameters rather than by inverting the surface: the
    corners' mean parameter names the point the flat triangle is standing in
    for, and evaluating it once is thousands of times cheaper than a numeric
    inversion — which is what made refining a procedural surface take minutes.
    """
    if not triangles or params is None or not len(params):
        return 0.0
    index = np.array(triangles, dtype=np.int64)
    if index.max() >= len(params):
        return 0.0
    centroids = np.asarray(vertices)[index].mean(axis=1)
    middles = params[index].mean(axis=1)
    worst = 0.0
    try:
        for centroid, (u, v) in zip(centroids, middles, strict=True):
            worst = max(
                worst, float(np.linalg.norm(surface.point_at(float(u), float(v)) - centroid))
            )
    except (GeometryError, ValueError):
        return 0.0
    return worst


def _refine_strip(
    surface: Surface,
    uv: np.ndarray,
    vertices: np.ndarray,
    triangles: list[tuple[int, int, int]],
    boundary: set[tuple[int, int]],
    tolerance: float,
):
    """Subdivide a strip until its triangles sit on the surface.

    An analytic surface says how far apart its rows may be, but a procedural
    one — a blend, an offset — has no such closed form, so the strip is
    measured and halved until it is inside tolerance.  Measuring is what makes
    this work for surfaces whose curvature is not known in advance.
    """
    rows = _v_subdivisions(surface, uv, tolerance)
    best = (vertices, triangles, uv)
    if rows > 1:
        best = _subdivide_strip(surface, uv, vertices, triangles, boundary, rows)
    if not isinstance(surface, SplineSurface):
        # An analytic surface states its own curvature, which is exact.
        # Measuring instead would have to average parameters across the seam,
        # where the two rings unwrap differently and the mean is meaningless.
        return best

    for _ in range(_MAX_REFINE_STEPS):
        if _deviation(surface, best[2], best[0], best[1]) <= tolerance:
            break
        if rows >= _MAX_ROWS:
            break
        rows = min(rows * 2, _MAX_ROWS)
        best = _subdivide_strip(surface, uv, vertices, triangles, boundary, rows)
    return best


def tessellate_face(
    face: Face, tolerance: float = DEFAULT_CHORD_TOLERANCE
) -> tuple[Mesh, str | None, float]:
    """Triangulate one face.

    Returns the mesh, a reason if it could not be built, and how far the
    triangles stray from the surface.  The deviation comes back with the mesh
    because the code that built the triangles is the only one that still knows
    their parameters, and recovering them afterwards costs an inversion each.
    """
    surface = face.surface
    if surface is None:
        return Mesh(), "no surface", 0.0
    if isinstance(surface, SplineSurface):
        if not TESSELLATE_SPLINE_SURFACES:
            return Mesh(), "spline surface (evaluation not verified)", 0.0
        if not surface.is_evaluable:
            return (
                Mesh(),
                f"no approximating spline for a {face.surface_entity.name} surface",
                0.0,
            )
        anchors = [
            vertex.position
            for edge in face.edges()
            for vertex in (edge.start, edge.end)
            if vertex is not None and vertex.position is not None
        ][:6]
        if anchors and not surface.fits(anchors, SURFACE_FIT_TOLERANCE):
            return Mesh(), "approximating spline does not match the face", 0.0

    rings: list[np.ndarray] = []
    for loop in face.loops():
        points = _loop_polyline(loop, tolerance)
        if points is None:
            return Mesh(), "spline or missing edge in boundary", 0.0
        if len(points) >= 3:
            rings.append(points)
    if not rings:
        return Mesh(), "no usable loop", 0.0
    if not isinstance(surface, SplineSurface) and not _rings_lie_on(surface, rings, tolerance):
        return Mesh(), "loop does not lie on the face's surface", 0.0

    try:
        uvs = [_to_uv(surface, ring) for ring in rings]
    except (GeometryError, ValueError):
        return Mesh(), "surface inversion failed", 0.0

    period = surface.u_period
    wrapping = [index for index, uv in enumerate(uvs) if _wraps(uv, period)]

    strip_uv: np.ndarray | None = None
    params: np.ndarray | None = None
    boundary: set[tuple[int, int]] = set()

    if len(wrapping) == 2 and len(rings) == 2:
        lower, upper = rings[wrapping[0]], rings[wrapping[1]]
        lower_uv, upper_uv = uvs[wrapping[0]], uvs[wrapping[1]]
        triangles, boundary = _stitch_ring(lower, upper, lower_uv, upper_uv)
        vertices = np.concatenate([lower, upper])
        strip_uv = np.concatenate([lower_uv, upper_uv])
        params = strip_uv
    elif wrapping:
        return Mesh(), "periodic face ezf3d cannot cut yet", 0.0
    elif len(rings) == 1 and not isinstance(surface, Plane):
        # A curved face is usually a band in parameter space.  Ear clipping
        # would fan across it and cut the chord; walking the two chains keeps
        # every triangle within one step of the boundary sampling.
        uv = uvs[0]
        # A band is monotone in one parameter or the other: along the axis for
        # a cylinder wall, around the tube for some fillets.  Try both before
        # falling back to a fan, which cuts the chord on a curved surface.
        chains = _monotone_chains(uv)
        swapped = False
        if chains is None:
            flipped = uv[:, ::-1].copy()
            chains = _monotone_chains(flipped)
            swapped = chains is not None
        if chains is None:
            triangles = ear_clip(uv if _signed_area(uv) > 0 else uv[::-1])
            if _signed_area(uv) <= 0:
                rings[0] = rings[0][::-1]
        else:
            triangles = _stitch_chains(uv[:, ::-1].copy() if swapped else uv, *chains)
            strip_uv = uv
            for chain in chains:
                for a, b in pairwise(chain):
                    boundary.add((min(a, b), max(a, b)))
        vertices = rings[0]
        params = uvs[0] if len(uvs[0]) == len(vertices) else None
    else:
        order = np.argsort([-abs(_signed_area(uv)) for uv in uvs])
        outer_index = int(order[0])
        outer_uv = uvs[outer_index]
        if _signed_area(outer_uv) < 0:
            outer_uv = outer_uv[::-1]
            rings[outer_index] = rings[outer_index][::-1]
        hole_uvs = []
        hole_rings = []
        for index in order[1:]:
            uv = uvs[int(index)]
            ring = rings[int(index)]
            if _signed_area(uv) > 0:
                uv, ring = uv[::-1], ring[::-1]
            hole_uvs.append(uv)
            hole_rings.append(ring)

        ordered = [rings[outer_index], *hole_rings]
        vertices = np.concatenate(ordered)
        params = np.concatenate([outer_uv, *hole_uvs])
        polygon_uv, origin = _bridge_holes(outer_uv, hole_uvs)
        local = ear_clip(polygon_uv)
        triangles = [(origin[a], origin[b], origin[c]) for a, b, c in local]

    if not triangles:
        return Mesh(), "triangulation produced nothing", 0.0

    if strip_uv is not None and boundary:
        vertices, triangles, params = _refine_strip(
            surface, strip_uv, vertices, triangles, boundary, tolerance
        )

    array = np.array(triangles, dtype=np.int64)
    # ASM's face sense says whether the face's outward normal agrees with its
    # surface; flip the winding when it does not.
    if not face.sense:
        array = array[:, ::-1]
    mesh = Mesh(vertices=vertices, triangles=array).cleaned()

    # Measured on the cleaned mesh: the ear clipper leaves a few zero-width
    # slivers along a hole's bridge, and a sliver's centroid can sit well off
    # the surface even though no real triangle does.
    if isinstance(surface, Plane) or mesh.is_empty:
        deviation = 0.0
    elif isinstance(surface, SplineSurface):
        deviation = _deviation(surface, params, mesh.vertices, mesh.triangles.tolist())
    else:
        # Analytic surfaces have an exact closed-form distance.
        deviation = max(
            (surface.distance_to(point) for point in mesh.corners().mean(axis=1)),
            default=0.0,
        )
    if deviation > tolerance * _REJECT_FACTOR:
        # A curved face whose parameter-space outline is monotone in neither
        # direction falls back to a fan, and a fan cuts the chord.  Emitting
        # those triangles would put centimetre errors into an exported mesh;
        # reporting the face is the honest option.
        return Mesh(), "triangulation exceeds tolerance on a curved face", deviation
    return mesh, None, deviation


def tessellate(
    shape: Shape, tolerance: float = DEFAULT_CHORD_TOLERANCE, *, measure: bool = True
) -> Tessellation:
    """Triangulate every face reachable from a body.

    Works one solid at a time: vertices are fused within a solid so its shared
    edges become shared indices, but never across solids, since two parts that
    merely touch are not one manifold.
    """
    result = Tessellation()
    total = Mesh()

    for solid in shape.solids():
        result.solids += 1
        solid_mesh = Mesh()
        seen: set[int] = set()
        skipped = 0
        brep_open = 0
        coedges_per_edge: Counter[int] = Counter()

        for face in solid.faces():
            if face.index in seen:
                continue
            seen.add(face.index)
            for loop in face.loops():
                for coedge in loop.coedges():
                    if coedge.edge is not None:
                        coedges_per_edge[coedge.edge.index] += 1

            mesh, reason, deviation = tessellate_face(face, tolerance)
            if reason is not None:
                result.unsupported[reason] += 1
                skipped += 1
                continue
            result.faces_meshed += 1
            solid_mesh = solid_mesh.merged(mesh)
            if measure:
                result.max_deviation = max(result.max_deviation, deviation)
                if deviation > tolerance * 2.0:
                    result.faces_over_tolerance += 1

        solid_mesh = solid_mesh.welded().cleaned()
        brep_open = sum(1 for count in coedges_per_edge.values() if count != 2)
        if not skipped and not brep_open and not solid_mesh.is_empty:
            result.closed_candidates += 1
            if solid_mesh.is_watertight:
                result.watertight_solids += 1
        total = total.merged(solid_mesh)

    result.mesh = total
    return result
