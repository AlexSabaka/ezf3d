"""Checking Fusion's cached mesh against the geometry ezf3d reads itself.

The cache and the ASM B-Rep are two independent accounts of the same solid:
one is what Fusion's tessellator produced, the other is what ezf3d derives
from the surface equations.  Comparing them tests both.

Two comparisons are worth making, and they answer different questions.

**Per face, against its surface.**  A cached face is matched to an ASM face,
and its vertices are measured against that face's surface.  This is the
stronger test — it does not go through ezf3d's tessellator at all, so it
checks the *reading* of cones, spheres, tori and splines rather than the
triangulating of them.  Analytic surfaces come out at 1e-07 cm, which is
float32 noise; spline surfaces do not, which is how the open question in
:mod:`ezf3d.asm.geometry` came to be stated as sharply as it is.

**Whole body, against the tessellation.**  A one-sided Hausdorff distance
from cached vertices to ezf3d's triangles, which is what
``ezf3d ogs --verify`` reports and what the tolerance in a design is quoted
against.

Matching is geometric because it has to be: cached faces are in Fusion's
display order, which is not the ASM record order, entity index order, or the
reverse of either — all three were tried and none matched more than three of
423.  So a cached face is paired with the ASM face whose boundary it encloses
and whose centroid is nearest, and only when that pairing is mutual.  Faces
that fail to pair are reported, never guessed at.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from ezf3d.asm.brep import Face, Shape
from ezf3d.asm.geometry import GeometryError
from ezf3d.mesh.mesh import Mesh
from ezf3d.mesh.tessellate import loop_polyline
from ezf3d.ogs.cache import GraphicsCache

#: How far a cached face's box may fall short of an ASM face's boundary before
#: the two are ruled out as a pair.  A tessellated curved face sits inside its
#: surface's box, never outside it, so the slack is one-sided in effect.
_CONTAINMENT_SLACK = 2e-3

#: Vertices sampled per face when measuring against a surface.  Surface
#: inversion is scalar and the distribution matters more than the count.
_SAMPLE = 60


@dataclass(frozen=True, slots=True)
class Measurement:
    """How far one cached face sits from the surface its B-Rep face names.

    Both numbers are needed because they answer different questions.  A face's
    interior vertices test the *surface*: they come out at float32 noise for
    every analytic kind.  Its boundary vertices test the *trimming curve* they
    were placed on, which for an intersection curve is an approximation — so a
    handful of them sit further out through no fault of the surface.  In one
    sample cylinder, five of six vertices are exact to 1e-07 cm and the sixth
    is 1.2e-02 out.  Reporting only the worst would call that a disagreement
    about the cylinder, which it is not.
    """

    #: Median distance over the face's vertices — the surface's own account.
    typical: float
    #: Greatest distance, which the boundary vertices dominate.
    worst: float


@dataclass(slots=True)
class Agreement:
    """How closely the cache and the B-Rep tell the same story."""

    cached_faces: int = 0
    brep_faces: int = 0
    matched: int = 0
    #: Measurements per surface class, over matched faces.
    by_surface: dict[str, list[Measurement]] = field(default_factory=lambda: defaultdict(list))
    #: Faces whose surface ezf3d cannot evaluate, so nothing could be measured.
    unevaluated: int = 0

    @property
    def unmatched(self) -> int:
        return self.cached_faces - self.matched

    def _pool(self, kind: str | None) -> list[Measurement]:
        if kind is not None:
            return self.by_surface.get(kind, [])
        return [item for values in self.by_surface.values() for item in values]

    def typical(self, kind: str | None = None) -> float:
        """Median across faces of each face's own median distance."""
        pool = self._pool(kind)
        return float(np.median([item.typical for item in pool])) if pool else 0.0

    def worst_typical(self, kind: str | None = None) -> float:
        """The least agreeable face, judged by its median rather than its tail."""
        pool = self._pool(kind)
        return max((item.typical for item in pool), default=0.0)

    def worst(self, kind: str | None = None) -> float:
        pool = self._pool(kind)
        return max((item.worst for item in pool), default=0.0)

    def summary(self) -> list[tuple[str, int, float, float, float]]:
        """``(surface, faces, typical, worst typical, worst)``, best first."""
        rows = [
            (
                kind,
                len(values),
                float(np.median([item.typical for item in values])),
                max(item.typical for item in values),
                max(item.worst for item in values),
            )
            for kind, values in self.by_surface.items()
        ]
        return sorted(rows, key=lambda row: row[2])


def _boundary(face: Face, tolerance: float) -> np.ndarray | None:
    pieces = [
        points for loop in face.loops() if (points := loop_polyline(loop, tolerance)) is not None
    ]
    return np.concatenate(pieces) if pieces else None


def match_faces(cache: GraphicsCache, shape: Shape, *, tolerance: float = 0.02) -> dict[int, Face]:
    """Pair cached faces with B-Rep faces, by index into :meth:`GraphicsCache.faces`.

    Only mutual nearest pairs whose cached box encloses the B-Rep boundary are
    returned, so the result is a partial matching by construction.
    """
    cached = cache.faces()
    if not cached:
        return {}
    faces = list(shape.faces())
    boundaries = [_boundary(face, tolerance) for face in faces]
    usable = [index for index, points in enumerate(boundaries) if points is not None]
    if not usable:
        return {}

    lower = np.array([boundaries[i].min(axis=0) for i in usable])  # type: ignore[union-attr]
    upper = np.array([boundaries[i].max(axis=0) for i in usable])  # type: ignore[union-attr]
    middle = np.array([boundaries[i].mean(axis=0) for i in usable])  # type: ignore[union-attr]

    points = [face.points for face in cached]
    cache_lower = np.array([p.min(axis=0) for p in points])
    cache_upper = np.array([p.max(axis=0) for p in points])
    cache_middle = np.array([p.mean(axis=0) for p in points])

    distance = np.linalg.norm(middle[:, None, :] - cache_middle[None, :, :], axis=2)
    outside = (lower[:, None, :] < cache_lower[None, :, :] - _CONTAINMENT_SLACK).any(2) | (
        upper[:, None, :] > cache_upper[None, :, :] + _CONTAINMENT_SLACK
    ).any(2)
    distance = np.where(outside, np.inf, distance)

    to_cache = distance.argmin(axis=1)
    to_brep = distance.argmin(axis=0)
    matched: dict[int, Face] = {}
    for row, column in enumerate(to_cache):
        if np.isinf(distance[row, column]) or to_brep[column] != row:
            continue
        matched[int(column)] = faces[usable[row]]
    return matched


def compare(cache: GraphicsCache, shape: Shape, *, tolerance: float = 0.02) -> Agreement:
    """Measure cached vertices against the surfaces their B-Rep faces name."""
    cached = cache.faces()
    pairs = match_faces(cache, shape, tolerance=tolerance)
    report = Agreement(cached_faces=len(cached), brep_faces=sum(1 for _ in shape.faces()))
    report.matched = len(pairs)
    for index, face in pairs.items():
        surface = face.surface
        if surface is None:
            report.unevaluated += 1
            continue
        points = cached[index].points
        if len(points) > _SAMPLE:
            points = points[:: len(points) // _SAMPLE + 1]
        try:
            distances = np.array([abs(surface.distance_to(point)) for point in points])
        except (GeometryError, ValueError, ZeroDivisionError):
            report.unevaluated += 1
            continue
        report.by_surface[type(surface).__name__].append(
            Measurement(typical=float(np.median(distances)), worst=float(distances.max()))
        )
    return report


def hausdorff(points: np.ndarray, mesh: Mesh, *, chunk: int = 2048) -> float:
    """Greatest distance from any of *points* to the nearest triangle of *mesh*.

    One-sided and exact — distance to the triangle, not to its nearest vertex,
    so a coarse mesh is not penalised for having few vertices.  Chunked over
    the query points because the full cross product would not fit.
    """
    if mesh.is_empty or not len(points):
        return 0.0
    corners = mesh.corners()
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    ab, ac = b - a, c - a
    worst = 0.0
    for start in range(0, len(points), chunk):
        block = points[start : start + chunk][:, None, :]
        worst = max(worst, float(_point_triangle(block, a, ab, ac).min(axis=1).max()))
    return worst


def _point_triangle(points: np.ndarray, a: np.ndarray, ab: np.ndarray, ac: np.ndarray):
    """Distance from each point to each triangle, clamped to the triangle."""
    ap = points - a[None, :, :]
    d00 = np.einsum("ij,ij->i", ab, ab)[None, :]
    d01 = np.einsum("ij,ij->i", ab, ac)[None, :]
    d11 = np.einsum("ij,ij->i", ac, ac)[None, :]
    d20 = np.einsum("kij,ij->ki", ap, ab)
    d21 = np.einsum("kij,ij->ki", ap, ac)
    determinant = d00 * d11 - d01 * d01
    determinant = np.where(np.abs(determinant) < 1e-30, 1e-30, determinant)
    v = (d11 * d20 - d01 * d21) / determinant
    w = (d00 * d21 - d01 * d20) / determinant
    # Clamp the barycentric coordinates into the triangle.  Projecting onto the
    # edges after clamping is what makes this the distance to the face rather
    # than to its plane.
    v = np.clip(v, 0.0, 1.0)
    w = np.clip(w, 0.0, 1.0)
    over = v + w > 1.0
    scale = np.where(over, v + w, 1.0)
    v = np.where(over, v / scale, v)
    w = np.where(over, w / scale, w)
    closest = a[None, :, :] + v[:, :, None] * ab[None, :, :] + w[:, :, None] * ac[None, :, :]
    return np.linalg.norm(points - closest, axis=2)
