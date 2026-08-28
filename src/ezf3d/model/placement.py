"""Where a sketch sits in space, recovered from the body it helped build.

A sketch's coordinates are two-dimensional in its own frame, and
:mod:`ezf3d.model.sketch` establishes that the design stream does not appear to
carry that frame.  This module recovers it from the geometry instead.

The idea is that an extrude sweeps a profile, so the profile's own loop turns up
as the boundary of a **planar face** of the resulting body.  Match the two and
the frame follows.

**Orthonormality is the proof, and it is not assumed.**  The fit solves

    world = origin + x * u_dir + y * v_dir

as a free affine map -- three unconstrained 3-vectors, nine numbers, nothing
requiring the axes to be perpendicular or unit length.  For a real match they
come out orthonormal anyway: over the samples the worst departure is 1e-9 and
the worst residual 7e-10 cm, and SUCKER's slot fits to 4e-15 cm with
``|u| = |v| = 1.000000000000``.  A wrong correspondence does not do that, which
is why the constraint is used as a *filter* rather than imposed on the solve.

**The match is selective.**  SUCKER's 0.5 mm slot matches 4 of the design's
1,178 planar faces, found by comparing the multiset of distances between a
loop's points -- a quantity no rigid motion changes.

**A second stream corroborates it.**  Sketch #31 drives extrude #32, whose
``AlongDistance`` is -0.2 mm.  Two of the matched faces sit at x = 3.1 and
x = 3.12 cm: exactly 0.02 cm apart, along their own normal.  The distance comes
from a parameter record in the design stream and the separation from vertex
positions in the ASM stream, parsed by unrelated code.

**What this cannot do is pick one.**  A design repeats its own shapes.  SUCKER's
slot lands in 8 distinct places, and that design's ``R-Pattern1-vCount`` is 8 --
the candidates *are* the pattern instances, and the sketch sits on one of them.
Nothing in the geometry says which, so a placement is reported as the set it is.
Roughly 40% of sketches match at all; the rest have been filleted, cut or
shelled since, and no face of the finished body still carries their outline.

See :doc:`unknowns </format/unknowns>` for what would settle it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from ezf3d.asm.brep import Shape
from ezf3d.asm.geometry import Plane
from ezf3d.model.sketch import Sketch, Sketches, read_sketches

#: Decimal places a distance is rounded to before loops are compared.  Loose
#: enough to survive a kernel's own rounding, tight enough that the 0.5 mm slot
#: matches 4 faces out of 1,178 rather than a useful fraction of them.
SIGNATURE_PLACES = 6

#: Worst distance from a placed sketch point to the vertex it was matched to.
#: Real fits land twelve orders of magnitude inside this.
RESIDUAL_TOLERANCE = 1e-7

#: How far the fitted axes may depart from orthonormal.  This is the check, not
#: an assumption: the solve is unconstrained and a correct match satisfies it
#: anyway.
ORTHONORMAL_TOLERANCE = 1e-9

#: Fewest points a loop needs before its shape says anything.
MIN_LOOP = 3


@dataclass(frozen=True, slots=True)
class Frame:
    """A sketch's plane: an origin and two axes, in centimetres."""

    origin: tuple[float, float, float]
    u_dir: tuple[float, float, float]
    v_dir: tuple[float, float, float]
    #: Worst distance from a placed point to the vertex it matched.
    residual: float = 0.0
    #: Worst departure of the fitted axes from orthonormal -- the evidence,
    #: since the solve does not require it.
    orthonormality: float = 0.0

    @property
    def normal(self) -> tuple[float, float, float]:
        n = np.cross(self.u_dir, self.v_dir)
        return (float(n[0]), float(n[1]), float(n[2]))

    def place(self, x: float, y: float) -> tuple[float, float, float]:
        """A sketch coordinate in world space."""
        p = np.asarray(self.origin) + x * np.asarray(self.u_dir) + y * np.asarray(self.v_dir)
        return (float(p[0]), float(p[1]), float(p[2]))


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one sketch may sit, and how many places that is."""

    sketch: int
    #: Curve ids of the loop that matched.
    loop: tuple[int, ...]
    #: Distinct frames, deduplicated by *where they put the profile* rather
    #: than by how the axes are labelled -- a rectangle's own symmetry gives
    #: several axis assignments that land it identically.
    frames: tuple[Frame, ...] = ()

    @property
    def is_unique(self) -> bool:
        return len(self.frames) == 1

    @property
    def best(self) -> Frame | None:
        """The tightest-fitting frame, when a caller wants one anyway."""
        return min(self.frames, key=lambda f: f.residual) if self.frames else None


@dataclass(slots=True)
class Placements:
    """Every sketch a document could be placed, and what could not be."""

    placements: list[Placement] = field(default_factory=list)
    #: Sketches whose loops matched no planar face at all.
    unplaced: int = 0
    #: Planar face loops the search had to compare against.
    faces: int = 0

    def __len__(self) -> int:
        return len(self.placements)

    def __iter__(self):
        return iter(self.placements)

    def unique(self) -> list[Placement]:
        """The sketches geometry places in exactly one spot."""
        return [row for row in self.placements if row.is_unique]

    def by_sketch(self) -> dict[int, list[Placement]]:
        """Rows per sketch — one per loop of it that matched."""
        found: dict[int, list[Placement]] = {}
        for row in self.placements:
            found.setdefault(row.sketch, []).append(row)
        return found

    def sketches_placed(self) -> int:
        return len(self.by_sketch())


def signature(points: Iterable[tuple[float, float]]) -> tuple[float, ...] | None:
    """The multiset of distances between points -- what no rigid motion changes."""
    array = np.asarray(list(points), dtype=float)
    if len(array) < MIN_LOOP:
        return None
    spread = np.linalg.norm(array[:, None, :] - array[None, :, :], axis=-1)
    upper = spread[np.triu_indices(len(array), 1)]
    return tuple(sorted(round(float(value), SIGNATURE_PLACES) for value in upper))


def _solve(flat: list[tuple[float, float]], world: list[np.ndarray]) -> Frame | None:
    """Least squares for ``world = origin + x*u + y*v``, with nothing imposed."""
    design = np.hstack([np.asarray(flat, dtype=float), np.ones((len(flat), 1))])
    target = np.asarray(world, dtype=float)
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    u_dir, v_dir, origin = solution
    residual = float(np.linalg.norm(design @ solution - target, axis=1).max())
    if residual > RESIDUAL_TOLERANCE:
        return None
    skew = max(
        abs(float(np.linalg.norm(u_dir)) - 1.0),
        abs(float(np.linalg.norm(v_dir)) - 1.0),
        abs(float(np.dot(u_dir, v_dir))),
    )
    if skew > ORTHONORMAL_TOLERANCE:
        return None
    return Frame(
        origin=tuple(float(v) for v in origin),  # type: ignore[arg-type]
        u_dir=tuple(float(v) for v in u_dir),  # type: ignore[arg-type]
        v_dir=tuple(float(v) for v in v_dir),  # type: ignore[arg-type]
        residual=residual,
        orthonormality=skew,
    )


def _fits(flat: list[tuple[float, float]], vertices: list[np.ndarray]) -> list[Frame]:
    """Every orthonormal frame taking a loop onto a face's vertices.

    Both traversal directions and every rotation of the cycle are tried,
    because nothing says the two loops start at the same corner or run the same
    way round.
    """
    found: list[Frame] = []
    count = len(flat)
    for reverse in (False, True):
        ordered = vertices[::-1] if reverse else vertices
        for shift in range(count):
            frame = _solve(flat, [ordered[(i + shift) % count] for i in range(count)])
            if frame is not None:
                found.append(frame)
    return found


def _landing(frame: Frame, flat: list[tuple[float, float]]) -> tuple[float, ...]:
    """Where a frame puts the whole profile -- the key candidates dedupe on."""
    placed = [frame.place(x, y) for x, y in sorted(flat)]
    return tuple(round(value, SIGNATURE_PLACES) for point in placed for value in point)


def _planar_loops(child) -> tuple[dict[tuple[float, ...], list[list[np.ndarray]]], int]:
    """Every planar face loop of a document, indexed by its shape."""
    index: dict[tuple[float, ...], list[list[np.ndarray]]] = {}
    faces = 0
    for body in child.bodies:
        for face in Shape(body.model()).faces():
            surface = face.surface
            if not isinstance(surface, Plane):
                continue
            faces += 1
            for loop in face.loops():
                vertices = [
                    coedge.edge.start.position
                    for coedge in loop.coedges()
                    if coedge.edge is not None
                    and coedge.edge.start is not None
                    and coedge.edge.start.position is not None
                ]
                key = signature([surface.invert(point) for point in vertices])
                if key is not None:
                    index.setdefault(key, []).append(vertices)
    return index, faces


def place_sketch(sketch: Sketch, index: dict) -> list[Placement]:
    """Candidate frames for one sketch, one entry per loop that matched."""
    at = {point.oid: (point.x, point.y) for point in sketch.points}
    rows: list[Placement] = []
    for loop in sketch.loops():
        if not loop.points:
            continue
        flat = [at[oid] for oid in loop.points if oid in at]
        if len(flat) != len(loop.points):
            continue
        key = signature(flat)
        if key is None:
            continue
        distinct: dict[tuple[float, ...], Frame] = {}
        for vertices in index.get(key, ()):
            if len(vertices) != len(flat):
                continue
            for frame in _fits(flat, vertices):
                distinct.setdefault(_landing(frame, flat), frame)
        if distinct:
            rows.append(
                Placement(
                    sketch=sketch.oid,
                    loop=loop.curves,
                    frames=tuple(distinct.values()),
                )
            )
    return rows


def place_sketches(child, sketches: Sketches | None = None) -> Placements:
    """Recover where a document's sketches sit, from its own bodies.

    Expensive: every body is parsed and every planar face loop measured, so
    this is opt-in rather than part of :func:`~ezf3d.model.sketch.read_sketches`.

    A sketch with no matching face is counted, not dropped -- most sketches have
    been filleted or cut since and no face still carries their outline.
    """
    if child.design is None or not child.bodies:
        return Placements()
    if sketches is None:
        sketches = read_sketches(child.design)
    if not len(sketches):
        return Placements()

    index, faces = _planar_loops(child)
    rows: list[Placement] = []
    unplaced = 0
    for sketch in sketches:
        found = place_sketch(sketch, index)
        rows.extend(found)
        unplaced += not found
    return Placements(placements=rows, unplaced=unplaced, faces=faces)


def swept_pair(placement: Placement, distance: float) -> tuple[Frame, ...]:
    """The frames whose profile has a twin *distance* away along its own normal.

    The cross-stream check: *distance* is an extrude's ``AlongDistance``, read
    from a parameter record, and the separation it predicts is between vertex
    positions in the ASM stream.  SUCKER's slot is the worked case -- two of its
    matched faces sit 0.02 cm apart, which is the -0.2 mm that extrude sweeps.
    """
    if distance <= 0.0:
        return ()
    kept: list[Frame] = []
    for frame in placement.frames:
        normal = np.asarray(frame.normal)
        here = float(np.dot(np.asarray(frame.origin), normal))
        for other in placement.frames:
            if other is frame:
                continue
            gap = abs(float(np.dot(np.asarray(other.origin), normal)) - here)
            if abs(gap - distance) < RESIDUAL_TOLERANCE:
                kept.append(frame)
                break
    return tuple(kept)


def spread(placements: Placements) -> dict[int, int]:
    """How many places each placed sketch could sit -- the ambiguity, counted."""
    counts: dict[int, int] = {}
    for row in placements:
        counts[len(row.frames)] = counts.get(len(row.frames), 0) + 1
    return dict(sorted(counts.items()))


__all__ = [
    "MIN_LOOP",
    "ORTHONORMAL_TOLERANCE",
    "RESIDUAL_TOLERANCE",
    "SIGNATURE_PLACES",
    "Frame",
    "Placement",
    "Placements",
    "place_sketch",
    "place_sketches",
    "signature",
    "spread",
    "swept_pair",
]
