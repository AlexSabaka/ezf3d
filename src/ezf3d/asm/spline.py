"""B-spline curves and surfaces from ASM subtype blocks.

ASM writes spline geometry as a *procedural* definition — a cylindrical
spline, a surface-surface blend, an offset — and, alongside it, an
**approximating B-spline** that the kernel itself uses to draw the thing.
Evaluating that approximation is how a viewer renders a blend without
reimplementing the blend, and it is what this module does.  Each block states
its own fit tolerance, so the approximation's error is known rather than
assumed.

Two encodings share the name ``nubs``, and they are told apart by shape rather
than by context: a curve writes one degree followed by a form enum, a surface
writes two degrees followed by four.  ``nurbs`` is the rational variant, with a
weight after each control point.

Knot vectors are stored with the first and last multiplicity one short of the
clamped form, so both ends get one more knot on the way in.  That is what makes
``len(knots) == len(control_points) + degree + 1`` come out right.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ezf3d.asm.tokens import Record, Tag

#: Blocks that hold an approximating surface.
SPLINE_SURFACE_KINDS = ("spl_sur",)
#: Blocks that hold an approximating curve.
SPLINE_CURVE_KINDS = ("int_cur", "par_cur")


class SplineError(ValueError):
    """Raised when a spline block cannot be read."""


def _expand_knots(pairs: list[tuple[float, int]]) -> np.ndarray:
    """Turn ``(value, multiplicity)`` pairs into a clamped knot vector."""
    if not pairs:
        raise SplineError("no knots")
    knots: list[float] = []
    for value, multiplicity in pairs:
        knots.extend([value] * max(int(multiplicity), 0))
    if not knots:
        raise SplineError("empty knot vector")
    # ASM leaves one off each end of the clamped vector.
    return np.array([knots[0], *knots, knots[-1]], dtype=float)


def _control_count(pairs: list[tuple[float, int]], degree: int) -> int:
    return sum(int(m) for _, m in pairs) + 2 - degree - 1


def _de_boor(degree: int, knots: np.ndarray, control: np.ndarray, t: float) -> np.ndarray:
    """Evaluate a B-spline at *t* by de Boor's algorithm."""
    count = len(control)
    lo, hi = knots[degree], knots[count]
    t = min(max(t, lo), hi)

    span = int(np.searchsorted(knots, t, side="right")) - 1
    span = min(max(span, degree), count - 1)

    points = [np.array(control[span - degree + j], dtype=float) for j in range(degree + 1)]
    for level in range(1, degree + 1):
        for j in range(degree, level - 1, -1):
            index = span - degree + j
            low, high = knots[index], knots[index + degree - level + 1]
            denominator = high - low
            alpha = 0.0 if denominator <= 0.0 else (t - low) / denominator
            points[j] = (1.0 - alpha) * points[j - 1] + alpha * points[j]
    return points[degree]


@dataclass(slots=True)
class BSplineCurve:
    """A non-rational or rational B-spline curve."""

    degree: int
    knots: np.ndarray
    #: ``(n, dim)`` control points; *dim* is 3 in model space, 2 in a surface's
    #: parameter space.
    control: np.ndarray
    weights: np.ndarray | None = None
    #: The fit tolerance ASM recorded for this approximation, in cm.
    fit_tolerance: float = 0.0

    @property
    def dimension(self) -> int:
        return int(self.control.shape[1])

    @property
    def domain(self) -> tuple[float, float]:
        return float(self.knots[self.degree]), float(self.knots[len(self.control)])

    def point_at(self, t: float) -> np.ndarray:
        if self.weights is None:
            return _de_boor(self.degree, self.knots, self.control, t)
        weighted = np.hstack([self.control * self.weights[:, None], self.weights[:, None]])
        result = _de_boor(self.degree, self.knots, weighted, t)
        divisor = result[-1] if abs(result[-1]) > 1e-30 else 1.0
        return result[:-1] / divisor

    def points_at(self, ts: np.ndarray) -> np.ndarray:
        return np.array([self.point_at(float(t)) for t in ts])


@dataclass(slots=True)
class BSplineSurface:
    """A tensor-product B-spline surface."""

    u_degree: int
    v_degree: int
    u_knots: np.ndarray
    v_knots: np.ndarray
    #: ``(n_u, n_v, 3)`` control net.
    control: np.ndarray
    weights: np.ndarray | None = None
    fit_tolerance: float = 0.0

    @property
    def u_domain(self) -> tuple[float, float]:
        return float(self.u_knots[self.u_degree]), float(self.u_knots[self.control.shape[0]])

    @property
    def v_domain(self) -> tuple[float, float]:
        return float(self.v_knots[self.v_degree]), float(self.v_knots[self.control.shape[1]])

    def _u_span(self, u: float) -> int:
        count = self.control.shape[0]
        lo, hi = self.u_domain
        u = min(max(u, lo), hi)
        span = int(np.searchsorted(self.u_knots, u, side="right")) - 1
        return min(max(span, self.u_degree), count - 1)

    def point_at(self, u: float, v: float) -> np.ndarray:
        """Evaluate by de Boor along *v*, then along *u* over the results.

        Only the ``u_degree + 1`` rows that actually influence *u* are
        evaluated.  Running every row instead is the difference between a
        tessellation that takes seconds and one that takes minutes, and the
        answer is identical — the other rows carry zero weight.
        """
        net = self.control
        if self.weights is not None:
            net = np.concatenate(
                [self.control * self.weights[..., None], self.weights[..., None]], axis=2
            )

        span = self._u_span(u)
        first = span - self.u_degree
        rows = np.array(
            [_de_boor(self.v_degree, self.v_knots, net[i], v) for i in range(first, span + 1)]
        )
        # De Boor along u over just those rows, using the matching knots.
        local_knots = self.u_knots[first : span + self.u_degree + 2]
        result = _de_boor(self.u_degree, local_knots, rows, u)
        if self.weights is None:
            return result
        divisor = result[-1] if abs(result[-1]) > 1e-30 else 1.0
        return result[:-1] / divisor


# -- reading ---------------------------------------------------------------


def _read_knot_pairs(tokens: Record, index: int, count: int) -> tuple[list, int]:
    pairs = []
    for _ in range(count):
        value = float(tokens[index][1])  # type: ignore[arg-type]
        multiplicity = int(tokens[index + 1][1])  # type: ignore[arg-type]
        pairs.append((value, multiplicity))
        index += 2
    return pairs, index


def _read_doubles(tokens: Record, index: int, count: int) -> tuple[np.ndarray, int]:
    values = []
    for _ in range(count):
        if index >= len(tokens) or tokens[index][0] != Tag.DOUBLE:
            raise SplineError(f"expected {count} reals, ran out after {len(values)}")
        values.append(float(tokens[index][1]))  # type: ignore[arg-type]
        index += 1
    return np.array(values, dtype=float), index


def is_surface_spline(tokens: Record, index: int) -> bool:
    """Does the ``nubs``/``nurbs`` at *index* describe a surface?

    A curve writes ``degree`` then a form enum; a surface writes two degrees.
    Reading the shape avoids having to know which kind of block we are inside.
    """
    return (
        index + 2 < len(tokens)
        and tokens[index + 1][0] == Tag.INT
        and tokens[index + 2][0] == Tag.INT
    )


def _skip_to_int(tokens: Record, index: int, limit: int = 8) -> int:
    """Advance past form and closure descriptors to the next integer.

    Their number varies — some surfaces carry an extra name token such as
    ``both`` between the degrees and the enums — so the count that follows is
    found rather than assumed.
    """
    for _ in range(limit):
        if index < len(tokens) and tokens[index][0] == Tag.INT:
            return index
        index += 1
    raise SplineError("no knot count after the spline header")


def read_curve_spline(
    tokens: Record, index: int, *, dimension: int = 3, rational: bool = False
) -> tuple[BSplineCurve, int]:
    """Read a curve ``nubs``/``nurbs`` whose name token sits at *index*."""
    i = index + 1
    degree = int(tokens[i][1])  # type: ignore[arg-type]
    i = _skip_to_int(tokens, i + 1)
    count = int(tokens[i][1])  # type: ignore[arg-type]
    i += 1
    pairs, i = _read_knot_pairs(tokens, i, count)

    control_count = _control_count(pairs, degree)
    if control_count < 1:
        raise SplineError(f"implausible control count {control_count}")
    stride = dimension + 1 if rational else dimension
    flat, i = _read_doubles(tokens, i, control_count * stride)
    grid = flat.reshape(control_count, stride)

    tolerance = 0.0
    if i < len(tokens) and tokens[i][0] == Tag.DOUBLE:
        tolerance = float(tokens[i][1])  # type: ignore[arg-type]
        i += 1

    return (
        BSplineCurve(
            degree=degree,
            knots=_expand_knots(pairs),
            control=grid[:, :dimension],
            weights=grid[:, dimension] if rational else None,
            fit_tolerance=tolerance,
        ),
        i,
    )


def read_surface_spline(
    tokens: Record, index: int, *, rational: bool = False
) -> tuple[BSplineSurface, int]:
    """Read a surface ``nubs``/``nurbs`` whose name token sits at *index*."""
    i = index + 1
    u_degree = int(tokens[i][1])  # type: ignore[arg-type]
    v_degree = int(tokens[i + 1][1])  # type: ignore[arg-type]
    i = _skip_to_int(tokens, i + 2)
    u_count = int(tokens[i][1])  # type: ignore[arg-type]
    v_count = int(tokens[i + 1][1])  # type: ignore[arg-type]
    i += 2
    u_pairs, i = _read_knot_pairs(tokens, i, u_count)
    v_pairs, i = _read_knot_pairs(tokens, i, v_count)

    n_u = _control_count(u_pairs, u_degree)
    n_v = _control_count(v_pairs, v_degree)
    if n_u < 1 or n_v < 1:
        raise SplineError(f"implausible control net {n_u} x {n_v}")
    stride = 4 if rational else 3
    flat, i = _read_doubles(tokens, i, n_u * n_v * stride)
    grid = flat.reshape(n_u, n_v, stride)

    tolerance = 0.0
    if i < len(tokens) and tokens[i][0] == Tag.DOUBLE:
        tolerance = float(tokens[i][1])  # type: ignore[arg-type]
        i += 1

    return (
        BSplineSurface(
            u_degree=u_degree,
            v_degree=v_degree,
            u_knots=_expand_knots(u_pairs),
            v_knots=_expand_knots(v_pairs),
            control=grid[:, :, :3],
            weights=grid[:, :, 3] if rational else None,
            fit_tolerance=tolerance,
        ),
        i,
    )


def _candidates(tokens: Record, start: int, end: int, shallow: bool):
    """Indices of ``nubs``/``nurbs`` tokens in ``[start, end)``.

    With *shallow* set, only those belonging to this block are offered: a
    procedural surface embeds the curves it was built from, and their splines
    would otherwise be mistaken for its own.
    """
    depth = 0
    for i in range(start, min(end, len(tokens))):
        tag, value = tokens[i]
        if tag == Tag.SUBTYPE_START:
            depth += 1
            continue
        if tag == Tag.SUBTYPE_END:
            depth -= 1
            continue
        if shallow and depth > 0:
            continue
        if tag in (Tag.ENTITY_TYPE, Tag.ENTITY_TYPE_EX) and value in ("nubs", "nurbs"):
            yield i, str(value)


def find_spline(
    tokens: Record, start: int, end: int, *, surface: bool, dimension: int = 3
) -> BSplineCurve | BSplineSurface | None:
    """First ``nubs``/``nurbs`` of the requested arity within ``[start, end)``.

    Only the block's own splines count.  Searching deeper finds the curves a
    procedural surface was built from, which are not the thing being asked for
    — chasing them produced a plausible-looking curve that missed its own
    vertices by two millimetres.  References out of the block are followed by
    the caller instead.
    """
    for shallow in (True,):
        for i, name in _candidates(tokens, start, end, shallow):
            if is_surface_spline(tokens, i) != surface:
                continue
            rational = name == "nurbs"
            try:
                if surface:
                    return read_surface_spline(tokens, i, rational=rational)[0]
                return read_curve_spline(tokens, i, dimension=dimension, rational=rational)[0]
            except (SplineError, IndexError, ValueError, TypeError):
                continue
    return None


def sample_domain(lo: float, hi: float, count: int) -> np.ndarray:
    return np.linspace(lo, hi, max(int(count), 2))


__all__ = [
    "BSplineCurve",
    "BSplineSurface",
    "SplineError",
    "find_spline",
    "is_surface_spline",
    "read_curve_spline",
    "read_surface_spline",
    "sample_domain",
]
