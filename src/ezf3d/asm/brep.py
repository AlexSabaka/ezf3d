"""Typed B-Rep traversal over an :class:`~ezf3d.asm.records.AsmModel`.

Wraps raw entities in the ACIS hierarchy — ``body -> lump -> shell -> face ->
loop -> coedge -> edge -> vertex`` — resolving by **base** class so tolerant
topology (``tedge``, ``tvertex``, ``tcoedge``) is handled without special cases.

Two facts drive the design:

*Sibling chains can be malformed.*  Every ``next``-chain walk is cycle-guarded;
a corrupt file yields a short chain rather than a hang.

*Not every record is live.*  A body that has been rolled back leaves stale
topology in the main section — records that resolve fine but whose vertices no
longer sit on their curves.  :meth:`Shape.bodies` walks from the ``body``
records down, so anything unreachable is simply never visited.  Across the
sample designs 4,874 analytic edges are unreachable this way, and they hold
every one of the large geometric inconsistencies.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from ezf3d.asm.geometry import Curve, Surface, read_curve, read_surface
from ezf3d.asm.records import NULL, AsmModel, Entity
from ezf3d.asm.tokens import Tag

#: Pointer slot holding the ``next`` sibling, for the classes that chain.
_NEXT_SLOT = 2


@dataclass(slots=True, frozen=True)
class Node:
    """An entity plus the model it lives in."""

    model: AsmModel
    entity: Entity

    @property
    def index(self) -> int:
        return self.entity.index

    def _at(self, slot: int) -> Entity | None:
        pointers = self.entity.pointers()
        if slot >= len(pointers):
            return None
        return self.model.resolve(pointers[slot])

    def _bool(self, which: int = 0) -> bool:
        seen = 0
        for tag, _ in self.entity.fields:
            if tag in (Tag.BOOL_TRUE, Tag.BOOL_FALSE):
                if seen == which:
                    return tag == Tag.BOOL_TRUE
                seen += 1
        return True

    def _reals(self) -> list[float]:
        return [float(v) for tag, v in self.entity.fields if tag == Tag.DOUBLE]  # type: ignore[arg-type]


def _chain(model: AsmModel, head: Entity | None, wrap) -> Iterator:
    """Walk a ``next`` chain from *head*, guarding against cycles."""
    seen: set[int] = set()
    current = head
    while current is not None and current.index not in seen:
        seen.add(current.index)
        yield wrap(model, current)
        pointers = current.pointers()
        nxt = pointers[_NEXT_SLOT] if len(pointers) > _NEXT_SLOT else NULL
        current = model.resolve(nxt)


@dataclass(slots=True, frozen=True)
class Vertex(Node):
    @property
    def position(self) -> np.ndarray | None:
        point = self._at(3)
        if point is None or not point.positions():
            return None
        return np.array(point.positions()[0], dtype=float)

    @property
    def is_tolerant(self) -> bool:
        return self.entity.name == "tvertex"


@dataclass(slots=True, frozen=True)
class Edge(Node):
    @property
    def start(self) -> Vertex | None:
        entity = self._at(2)
        return Vertex(self.model, entity) if entity is not None else None

    @property
    def end(self) -> Vertex | None:
        entity = self._at(3)
        return Vertex(self.model, entity) if entity is not None else None

    @property
    def curve(self) -> Curve | None:
        entity = self._at(5)
        return read_curve(entity) if entity is not None else None

    @property
    def curve_entity(self) -> Entity | None:
        return self._at(5)

    @property
    def sense(self) -> bool:
        """True when the edge runs along the curve's own direction."""
        return self._bool()

    @property
    def range(self) -> tuple[float, float]:
        """The edge's parameter range, in the curve's parameterisation.

        Stored in curve order; :attr:`sense` says which way the edge runs, and
        a reversed edge evaluates the curve at ``-t``.
        """
        reals = self._reals()
        return (reals[0], reals[1]) if len(reals) >= 2 else (0.0, 0.0)

    @property
    def is_closed(self) -> bool:
        """Start and end are the same vertex.

        Usually a **closed** edge — a full circle such as a cylinder's rim,
        whose parameter range spans a whole period.  Only when it also lacks a
        curve is it a true singularity; see :attr:`is_degenerate`.
        """
        pointers = self.entity.pointers()
        return len(pointers) > 3 and pointers[2] == pointers[3]

    @property
    def is_degenerate(self) -> bool:
        """A cone apex or sphere pole: collapsed to a point, with no curve."""
        return self.is_closed and self.curve_entity is None

    @property
    def is_tolerant(self) -> bool:
        return self.entity.name == "tedge"

    def parameter(self, t: float) -> float:
        """Map a stored parameter into the curve's own parameterisation.

        A reversed edge runs the curve backwards, so its stored parameters
        evaluate at ``-t``.
        """
        return t if self.sense else -t

    def derived_range(self) -> tuple[float, float] | None:
        """Parameters recovered by inverting the curve at the two vertices.

        Prefer this to :attr:`range` when discretising.  ASM writes a sentinel
        parameter range on some edges — a one-centimetre edge carrying
        ``(-100, +100)`` — so the stored numbers are a hint while the vertices
        are authoritative.  Across the sample designs 504 of 95,668 endpoints
        (0.53 %) sit on a sentinel range.
        """
        curve = self.curve
        start, end = self.start, self.end
        if curve is None or start is None or end is None:
            return None
        if start.position is None or end.position is None:
            return None
        return curve.invert(start.position), curve.invert(end.position)

    def range_is_sentinel(self, tol: float = 1e-6) -> bool:
        """True when :attr:`range` does not describe the edge's real extent.

        Either end may be the one that disagrees, so both are checked.
        """
        curve = self.curve
        start, end = self.start, self.end
        if curve is None or start is None or end is None:
            return False
        if start.position is None or end.position is None:
            return False
        t0, t1 = self.range
        for t, vertex in ((t0, start), (t1, end)):
            try:
                at_stored = curve.point_at(self.parameter(t))
            except Exception:
                return True
            if float(np.linalg.norm(at_stored - vertex.position)) > tol:
                return True
        return False

    def endpoints(self) -> tuple[np.ndarray, np.ndarray] | None:
        """The edge's two ends, taken from its vertices."""
        start, end = self.start, self.end
        if start is None or end is None:
            return None
        if start.position is None or end.position is None:
            return None
        return start.position, end.position


@dataclass(slots=True, frozen=True)
class Coedge(Node):
    @property
    def edge(self) -> Edge | None:
        entity = self._at(5)
        return Edge(self.model, entity) if entity is not None else None

    @property
    def sense(self) -> bool:
        """True when the coedge runs along its edge's direction."""
        return self._bool()

    @property
    def partner(self) -> Coedge | None:
        entity = self._at(4)
        return Coedge(self.model, entity) if entity is not None else None

    @property
    def pcurve_entity(self) -> Entity | None:
        pointers = self.entity.pointers()
        return self.model.resolve(pointers[7]) if len(pointers) > 7 else None


@dataclass(slots=True, frozen=True)
class Loop(Node):
    def coedges(self) -> Iterator[Coedge]:
        """Walk the closed ring of coedges, guarding against cycles."""
        pointers = self.entity.pointers()
        start = pointers[3] if len(pointers) > 3 else NULL
        seen: set[int] = set()
        current = self.model.resolve(start)
        while current is not None and current.index not in seen:
            seen.add(current.index)
            yield Coedge(self.model, current)
            nxt = current.pointers()[_NEXT_SLOT]
            current = self.model.resolve(nxt)


@dataclass(slots=True, frozen=True)
class Face(Node):
    def loops(self) -> Iterator[Loop]:
        yield from _chain(self.model, self._at(3), Loop)

    @property
    def surface(self) -> Surface | None:
        entity = self._at(6)
        return read_surface(entity) if entity is not None else None

    @property
    def surface_entity(self) -> Entity | None:
        return self._at(6)

    @property
    def sense(self) -> bool:
        """True when the face normal agrees with its surface normal."""
        return self._bool()

    def edges(self) -> Iterator[Edge]:
        for loop in self.loops():
            for coedge in loop.coedges():
                edge = coedge.edge
                if edge is not None:
                    yield edge


@dataclass(slots=True, frozen=True)
class Shell(Node):
    def faces(self) -> Iterator[Face]:
        yield from _chain(self.model, self._at(4), Face)


@dataclass(slots=True, frozen=True)
class Lump(Node):
    def shells(self) -> Iterator[Shell]:
        yield from _chain(self.model, self._at(3), Shell)


@dataclass(slots=True, frozen=True)
class Body(Node):
    def lumps(self) -> Iterator[Lump]:
        yield from _chain(self.model, self._at(2), Lump)

    @property
    def transform_entity(self) -> Entity | None:
        pointers = self.entity.pointers()
        return self.model.resolve(pointers[4]) if len(pointers) > 4 else None

    def faces(self) -> Iterator[Face]:
        for lump in self.lumps():
            for shell in lump.shells():
                yield from shell.faces()

    def edges(self) -> Iterator[Edge]:
        """Every distinct edge reachable from this body."""
        seen: set[int] = set()
        for face in self.faces():
            for edge in face.edges():
                if edge.index not in seen:
                    seen.add(edge.index)
                    yield edge


class Shape:
    """The live topology of an ASM model.

    A file with rollback history can carry several ``body`` records that all
    describe the *same* solid — one per saved state, sharing one lump and shell
    chain.  Traversal therefore de-duplicates by entity index, so a face is
    visited once however many bodies reach it.
    """

    __slots__ = ("model",)

    def __init__(self, model: AsmModel) -> None:
        self.model = model

    def bodies(self) -> Iterator[Body]:
        """Every ``body`` record, including historical duplicates."""
        for entity in self.model.entities:
            if entity.base == "body":
                yield Body(self.model, entity)

    def solids(self) -> Iterator[Body]:
        """One :class:`Body` per distinct solid, keyed by its lump chain."""
        seen: set[tuple[int, ...]] = set()
        for body in self.bodies():
            key = tuple(lump.index for lump in body.lumps())
            if key in seen:
                continue
            seen.add(key)
            yield body

    def faces(self) -> Iterator[Face]:
        seen: set[int] = set()
        for body in self.bodies():
            for face in body.faces():
                if face.index not in seen:
                    seen.add(face.index)
                    yield face

    def edges(self) -> Iterator[Edge]:
        """Every distinct edge reachable from a body.

        Records not reachable this way are stale rollback residue — they
        resolve, but their vertices no longer lie on their curves.
        """
        seen: set[int] = set()
        for body in self.bodies():
            for edge in body.edges():
                if edge.index not in seen:
                    seen.add(edge.index)
                    yield edge

    def reachable_indices(self) -> set[int]:
        """Indices of every entity reachable from a body, for orphan analysis."""
        found: set[int] = set()
        for body in self.bodies():
            found.add(body.index)
            for face in body.faces():
                found.add(face.index)
                for loop in face.loops():
                    found.add(loop.index)
                    for coedge in loop.coedges():
                        found.add(coedge.index)
                        edge = coedge.edge
                        if edge is not None:
                            found.add(edge.index)
        return found
