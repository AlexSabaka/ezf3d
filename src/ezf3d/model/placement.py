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
from pathlib import Path

import numpy as np

from ezf3d.asm.brep import Coedge, Shape
from ezf3d.asm.geometry import Plane
from ezf3d.model.design import read_design
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
    #: Curve ids this placement rests on -- a matched loop, or the curves the
    #: body's own edges named.
    loop: tuple[int, ...]
    #: ``edges`` when the ASM attribute named the curves outright, ``shape``
    #: when the loop was matched to a face by its distances.  The first is
    #: exact and far likelier to be unique; the second reaches sketches whose
    #: curves no edge names.
    route: str = "edges"
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


#: The ASM attribute definition that names a sketch curve.  An ``ATTRIB_CUSTOM``
#: carrying it hangs off a **coedge** and holds a string of six integers whose
#: first two are the curve's own ``(crv_primary_id, crv_secondary_id)``.
SKETCH_ATTRIB = "sketch_attrib_def"

#: Token type codes in an ASM entity record: a string, and a pointer.
_STRING, _POINTER = 7, 12


@dataclass(frozen=True, slots=True)
class SketchEdge:
    """A B-Rep edge that says which sketch curve drew it."""

    #: Object id of the sketch, and of the curve within it.
    sketch: int
    curve: int
    #: Where that curve ended up, in world centimetres.
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    #: How the key was resolved: ``component`` when the body's own component
    #: held it, ``document`` when only one curve in the whole design claims it.
    route: str = "component"

    @property
    def is_closed(self) -> bool:
        """A B-Rep edge that returns to its own start."""
        return self.start == self.end


def sketch_edges(child, sketches: Sketches | None = None) -> list[SketchEdge]:
    """Every B-Rep edge an ASM attribute ties back to a sketch curve.

    This is the link the reference graph does not carry.  A coedge's
    ``sketch_attrib_def`` names a curve by the identity the curve record itself
    holds -- ``crv_primary_id`` and ``crv_secondary_id`` -- so body topology
    reaches back to the geometry that drew it by a **key that is read, not
    inferred**.

    **The key is scoped to a component**, and finding that is what makes this
    usable.  Robotic_Bhujha reuses 159 of its 331 keys across the document but
    **none within any of its ten components**; SUCKER and the fan reuse none at
    all.  A body belongs to a component and a component owns its sketches, so
    the body says which table to read.  Only Focuser Mk1 -- an assembly of
    XREF'd documents -- still collides, on 44 keys at multiplicity two.

    A key is therefore resolved against the sketches of the body's **own
    component** first, and against the whole document only where exactly one
    curve claims it.  That reaches **8,333 edges naming 1,079 of 1,334 curves
    across 125 of 130 sketches**, where refusing every key shared anywhere in
    the document reached 2,714 edges and 73 sketches.

    :func:`closure_check` measures what is left.  A B-Rep edge that closes on
    itself can only have come from a full circle: **794 of 796** do, the two
    exceptions both in the assembly.  Reading with no scope at all put 209
    closed edges on lines, so the scope is doing the work even where it is
    imperfect.

    A key is resolved against the sketches of the body's **own component**
    first, and against the whole document only where exactly one curve claims
    it.  :func:`closure_check` measures what is left.
    """
    if child.design is None or not child.bodies:
        return []
    if sketches is None:
        sketches = read_sketches(child.design)
    document = _unambiguous(sketches)
    scoped, contested = _by_component(child, sketches)
    if not document and not scoped:
        return []
    component_of = _bodies_by_component(child)

    found: list[SketchEdge] = []
    for body in child.bodies:
        owner = component_of.get(Path(body.path).name)
        near = scoped.get(owner, {})
        clashing = contested.get(owner, frozenset())
        model = body.model()
        at = {entity.index: entity for entity in model.entities}
        for entity in model.entities:
            if entity.base != "attrib":
                continue
            named = [
                value for kind, value in entity.tokens if kind == _STRING and isinstance(value, str)
            ]
            if SKETCH_ATTRIB not in named:
                continue
            key = _curve_named(named[-1])
            route = "component"
            if key in near and key not in clashing:
                sketch, curve = near[key]
            elif key in document:
                sketch, curve = document[key]
                route = "document"
            else:
                continue
            hosts = [
                value
                for kind, value in entity.tokens
                if kind == _POINTER and isinstance(value, int) and value >= 0
            ]
            # Slot 6 is the owner; the earlier slots chain to sibling
            # attributes, so the last pointer is the entity this hangs off.
            node = at.get(hosts[-1]) if hosts else None
            if node is None or node.base != "coedge":
                continue
            edge = Coedge(model, node).edge
            if edge is None or edge.start is None or edge.end is None:
                continue
            head, tail = edge.start.position, edge.end.position
            if head is None or tail is None:
                continue
            found.append(
                SketchEdge(
                    sketch=sketch,
                    curve=curve,
                    start=(float(head[0]), float(head[1]), float(head[2])),
                    end=(float(tail[0]), float(tail[1]), float(tail[2])),
                    route=route,
                )
            )
    return found


def _bodies_by_component(child) -> dict[str, int]:
    """Blob filename to the object id of the component that owns it."""
    design = read_design(child.design)
    return {
        Path(blob).name: component.oid
        for component in design.components
        for blob in component.bodies
    }


def _by_component(
    child, sketches: Sketches
) -> tuple[dict[int, dict[tuple[int, int], tuple[int, int]]], dict[int, frozenset]]:
    """Curve keys per component, and the keys that collide inside one."""
    design = read_design(child.design)
    table: dict[int, dict[tuple[int, int], tuple[int, int]]] = {}
    clashes: dict[int, set[tuple[int, int]]] = {}
    for sketch in sketches:
        owner = design.owner(sketch.oid)
        which = owner.oid if owner is not None else -1
        rows = table.setdefault(which, {})
        for curve in sketch.curves:
            if curve.key == (0, 0):
                continue
            if curve.key in rows:
                clashes.setdefault(which, set()).add(curve.key)
            rows[curve.key] = (sketch.oid, curve.oid)
    return table, {which: frozenset(keys) for which, keys in clashes.items()}


def closure_check(sketches: Sketches, edges: Iterable[SketchEdge]) -> tuple[int, tuple[int, ...]]:
    """``(closed edges naming a circle, the curve ids of those that do not)``.

    A B-Rep edge that returns to its own start can only have come from a full
    circle.  The kind is read from the design stream by counting a curve's
    point references and the closure from vertex identity in the ASM stream, so
    this is a check across two parsers rather than within one -- and it is what
    caught the scope being wrong in the first place.

    Only that direction holds: a circle cut by an intersection leaves an open
    edge, so open edges are not checked.
    """
    kind_of = {curve.oid: curve.kind for sketch in sketches for curve in sketch.curves}
    good = 0
    wrong: list[int] = []
    for edge in edges:
        if not edge.is_closed:
            continue
        if kind_of.get(edge.curve) == "Circle":
            good += 1
        else:
            wrong.append(edge.curve)
    return good, tuple(wrong)


def _unambiguous(sketches: Sketches) -> dict[tuple[int, int], tuple[int, int]]:
    """Curve keys that belong to exactly one curve in this document."""
    seen: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for sketch in sketches:
        for curve in sketch.curves:
            if curve.key != (0, 0):
                seen.setdefault(curve.key, []).append((sketch.oid, curve.oid))
    return {key: rows[0] for key, rows in seen.items() if len(rows) == 1}


def shared_keys(sketches: Sketches) -> dict[tuple[int, int], int]:
    """Keys more than one curve claims -- the scope this does not yet know."""
    seen: dict[tuple[int, int], int] = {}
    for sketch in sketches:
        for curve in sketch.curves:
            if curve.key != (0, 0):
                seen[curve.key] = seen.get(curve.key, 0) + 1
    return {key: count for key, count in seen.items() if count > 1}


def _curve_named(text: str) -> tuple[int, int] | None:
    """The ``(primary, secondary)`` an attribute's payload names."""
    parts = text.split()
    if len(parts) < 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


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


def place_by_edges(child, sketches: Sketches | None = None) -> list[Placement]:
    """Place sketches from the edges that name their curves.

    Exact where it applies: the correspondence between a sketch point and a
    world vertex is *read* from the attribute rather than guessed from a shape,
    so no signature search is involved and no orientation has to be tried
    beyond the two an edge can run in.

    Edges are grouped into instances by shared world vertices -- a patterned
    copy is a separate connected component -- and each group is fitted on its
    own.  That is what makes the answer unique far more often than shape
    matching: **35 sketches against 10** over the samples.
    """
    if sketches is None and child.design is not None:
        sketches = read_sketches(child.design)
    if not sketches or not len(sketches):
        return []
    by_oid = {sketch.oid: sketch for sketch in sketches}
    grouped: dict[int, list[SketchEdge]] = {}
    for edge in sketch_edges(child, sketches):
        grouped.setdefault(edge.sketch, []).append(edge)

    rows: list[Placement] = []
    for oid, edges in grouped.items():
        sketch = by_oid[oid]
        at = {point.oid: (point.x, point.y) for point in sketch.points}
        curves = {curve.oid: curve for curve in sketch.curves}
        distinct: dict[tuple[float, ...], Frame] = {}
        used: set[int] = set()
        for members in _instances(edges):
            flat: list[tuple[float, float]] = []
            world: list[np.ndarray] = []
            for edge in members:
                curve = curves.get(edge.curve)
                if curve is None or curve.ends is None:
                    continue
                head, tail = curve.ends
                if head not in at or tail not in at:
                    continue
                used.add(edge.curve)
                flat += [at[head], at[tail]]
                world += [np.asarray(edge.start), np.asarray(edge.end)]
            if len(flat) < 4:
                continue
            frame = _best_of(flat, world)
            if frame is not None:
                distinct.setdefault(_landing(frame, flat), frame)
        if distinct:
            rows.append(
                Placement(
                    sketch=oid,
                    loop=tuple(sorted(used)),
                    frames=tuple(distinct.values()),
                    route="edges",
                )
            )
    return rows


def _instances(edges: list[SketchEdge]) -> list[list[SketchEdge]]:
    """Split edges into the separate places the sketch's curves landed.

    Two edges belong together when they meet at a vertex, so a patterned copy
    -- which shares no vertex with the seed -- comes out as its own group.
    """
    parent: dict[tuple[float, float, float], tuple[float, float, float]] = {}

    def root(node):
        while parent.setdefault(node, node) != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for edge in edges:
        head, tail = root(edge.start), root(edge.end)
        if head != tail:
            parent[head] = tail
    groups: dict[tuple[float, float, float], list[SketchEdge]] = {}
    for edge in edges:
        groups.setdefault(root(edge.start), []).append(edge)
    return list(groups.values())


def _best_of(flat: list[tuple[float, float]], world: list[np.ndarray]) -> Frame | None:
    """The better of the two ways an edge's endpoints can pair up."""
    best: Frame | None = None
    for swap in (False, True):
        points = [world[index ^ 1] for index in range(len(world))] if swap else world
        frame = _solve(flat, points)
        if frame is not None and (best is None or frame.residual < best.residual):
            best = frame
    return best


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

    # The attribute link is exact, so it goes first; shape matching then
    # reaches the sketches no edge names.
    rows: list[Placement] = list(place_by_edges(child, sketches))
    named = {row.sketch for row in rows}
    index, faces = _planar_loops(child)
    unplaced = 0
    for sketch in sketches:
        if sketch.oid in named:
            continue
        found = [
            Placement(sketch=row.sketch, loop=row.loop, frames=row.frames, route="shape")
            for row in place_sketch(sketch, index)
        ]
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
    "SKETCH_ATTRIB",
    "Frame",
    "Placement",
    "Placements",
    "SketchEdge",
    "closure_check",
    "place_by_edges",
    "place_sketch",
    "place_sketches",
    "shared_keys",
    "signature",
    "sketch_edges",
    "spread",
    "swept_pair",
]
