"""B-Rep topology and geometry census over an :class:`~ezf3d.asm.records.AsmModel`.

ASM's topology is the ACIS hierarchy: ``body -> lump -> shell -> face -> loop
-> coedge -> edge -> vertex -> point``.  Geometry hangs off it as ``surface``
subclasses (``plane``, ``cone``, ``sphere``, ``torus``, ``spline``) and
``curve`` subclasses (``straight``, ``ellipse``, ``intcurve``).

Everything here is counting and bounds — evaluating the surfaces is Phase 2.

Fusion's kernel works in **centimetres**; the design stream's unit system is
``CmMKS``.  Bounds are reported in cm to stay faithful to the file.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ezf3d.asm.records import AsmModel

#: ASM topology classes, outermost first.
TOPOLOGY_CLASSES = (
    "body",
    "lump",
    "shell",
    "subshell",
    "face",
    "loop",
    "coedge",
    "edge",
    "vertex",
    "wire",
)

#: Analytic surface classes we can evaluate without a spline kernel.
ANALYTIC_SURFACES = frozenset({"plane", "cone", "sphere", "torus"})
#: Analytic curve classes.
ANALYTIC_CURVES = frozenset({"straight", "ellipse"})

#: Fusion's kernel unit.
KERNEL_UNIT = "cm"


@dataclass(slots=True)
class Bounds:
    """Axis-aligned bounds, in kernel units (cm)."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(hi - lo for lo, hi in zip(self.min, self.max, strict=True))  # type: ignore[return-value]

    @property
    def diagonal(self) -> float:
        return math.dist(self.min, self.max)

    def as_mm(self) -> Bounds:
        return Bounds(
            tuple(v * 10 for v in self.min),  # type: ignore[arg-type]
            tuple(v * 10 for v in self.max),  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class TopologyCensus:
    """What a body file contains."""

    entities: int = 0
    topology: Counter[str] = field(default_factory=Counter)
    surfaces: Counter[str] = field(default_factory=Counter)
    curves: Counter[str] = field(default_factory=Counter)
    attributes: Counter[str] = field(default_factory=Counter)
    other: Counter[str] = field(default_factory=Counter)
    #: Bounds of the ``point`` entities — every B-Rep vertex, so a true bound
    #: on the vertices but not on bulged faces between them.
    vertex_bounds: Bounds | None = None

    # Convenience accessors used by the CLI and by tests.
    @property
    def bodies(self) -> int:
        return self.topology["body"]

    @property
    def lumps(self) -> int:
        return self.topology["lump"]

    @property
    def shells(self) -> int:
        return self.topology["shell"]

    @property
    def faces(self) -> int:
        return self.topology["face"]

    @property
    def loops(self) -> int:
        return self.topology["loop"]

    @property
    def coedges(self) -> int:
        return self.topology["coedge"]

    @property
    def edges(self) -> int:
        return self.topology["edge"]

    @property
    def vertices(self) -> int:
        return self.topology["vertex"]

    @property
    def analytic_only(self) -> bool:
        """True when no spline geometry is present — Phase 2 can tessellate it
        exactly from analytic surfaces alone."""
        return not (set(self.surfaces) - ANALYTIC_SURFACES) and not (
            set(self.curves) - ANALYTIC_CURVES
        )

    @property
    def spline_fraction(self) -> float:
        """Share of surfaces that need spline evaluation."""
        total = sum(self.surfaces.values())
        if not total:
            return 0.0
        return sum(n for k, n in self.surfaces.items() if k not in ANALYTIC_SURFACES) / total


def census(model: AsmModel) -> TopologyCensus:
    """Count topology and geometry, and bound the vertices."""
    result = TopologyCensus(entities=len(model))
    topo = set(TOPOLOGY_CLASSES)
    lo = [math.inf] * 3
    hi = [-math.inf] * 3

    for entity in model.entities:
        name, base = entity.name, entity.base
        if name in topo:
            result.topology[name] += 1
        elif base == "surface":
            result.surfaces[name] += 1
        elif base == "curve":
            result.curves[name] += 1
        elif base == "attrib":
            result.attributes[name] += 1
        else:
            result.other[name] += 1

        if name == "point":
            for pos in entity.positions():
                for axis in range(3):
                    lo[axis] = min(lo[axis], pos[axis])
                    hi[axis] = max(hi[axis], pos[axis])

    if lo[0] != math.inf:
        result.vertex_bounds = Bounds(tuple(lo), tuple(hi))  # type: ignore[arg-type]
    return result
