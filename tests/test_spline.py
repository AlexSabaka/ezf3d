"""B-spline reading and evaluation.

ASM stores spline geometry procedurally — a blend, an offset, a helical sweep —
with an approximating B-spline beside it.  These check that the approximation
is read correctly, that it is the *right* one, and that ezf3d refuses it when
it is not.
"""

from __future__ import annotations

import numpy as np
import pytest

import ezf3d
from ezf3d.asm.brep import Shape
from ezf3d.asm.geometry import SplineCurve, SplineSurface
from ezf3d.asm.spline import (
    BSplineCurve,
    BSplineSurface,
    SplineError,
    _de_boor,
    _expand_knots,
)
from ezf3d.mesh import usable_curve
from ezf3d.mesh.polyline import SPLINE_FIT_TOLERANCE


def test_de_boor_reproduces_a_bezier():
    """A clamped cubic with four control points is a Bezier curve."""
    control = np.array([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [2, 1, 0]])
    curve = BSplineCurve(3, _expand_knots([(0.0, 3), (1.0, 3)]), control)
    assert curve.domain == (0.0, 1.0)
    assert np.allclose(curve.point_at(0.0), control[0])
    assert np.allclose(curve.point_at(1.0), control[-1])
    # Bernstein form at the midpoint.
    expected = (control[0] + 3 * control[1] + 3 * control[2] + control[3]) / 8.0
    assert np.allclose(curve.point_at(0.5), expected)


def test_knot_vector_is_clamped_on_the_way_in():
    """ASM stores one multiplicity short at each end.

    The relation ``len(knots) == len(control) + degree + 1`` is what says the
    extra pair belongs there.
    """
    knots = _expand_knots([(0.0, 3), (0.5, 1), (1.0, 3)])
    assert list(knots) == [0.0, 0.0, 0.0, 0.0, 0.5, 1.0, 1.0, 1.0, 1.0]
    control_count = sum(m for _, m in [(0.0, 3), (0.5, 1), (1.0, 3)]) + 2 - 3 - 1
    assert len(knots) == control_count + 3 + 1


def test_empty_knots_are_rejected():
    with pytest.raises(SplineError):
        _expand_knots([])


def test_surface_evaluation_uses_only_the_rows_that_matter():
    """The localised evaluation must equal the full tensor product exactly."""
    rng = np.random.default_rng(11)
    u_pairs = [(0.0, 3), (1.0, 1), (2.0, 1), (3.0, 3)]
    v_pairs = [(0.0, 2), (1.0, 1), (2.0, 2)]
    n_u = sum(m for _, m in u_pairs) + 2 - 3 - 1
    n_v = sum(m for _, m in v_pairs) + 2 - 2 - 1
    control = rng.normal(size=(n_u, n_v, 3))
    surface = BSplineSurface(3, 2, _expand_knots(u_pairs), _expand_knots(v_pairs), control)

    def full(u, v):
        rows = np.array([_de_boor(2, surface.v_knots, control[i], v) for i in range(n_u)])
        return _de_boor(3, surface.u_knots, rows, u)

    for u in np.linspace(*surface.u_domain, 7):
        for v in np.linspace(*surface.v_domain, 7):
            assert np.allclose(surface.point_at(u, v), full(u, v), atol=1e-12)


def test_interned_definitions_all_resolve(opened):
    """Every ``ref`` must name a real definition.

    The index counts bracketed blocks at any depth, references excluded — a
    rule several other numberings also keep in range, so it is checked by what
    the references have to *mean* as well as by not overflowing.
    """
    from ezf3d.asm.tokens import Tag

    def surfaceish(name: str) -> bool:
        return name.endswith(("spl_sur", "spl_line")) or name in (
            "spline",
            "plane",
            "cone",
            "sphere",
            "torus",
        )

    total = unresolved = surface_refs = surface_hits = 0
    for child in opened.documents():
        for body in child.bodies:
            model = body.model()
            for entity in model.entities:
                tokens = entity.tokens
                for i, (tag, _value) in enumerate(tokens):
                    if tag != Tag.SUBTYPE_START:
                        continue
                    if i + 2 < len(tokens) and tokens[i + 1][1] == "ref":
                        total += 1
                        block = model.resolve_subtype(int(tokens[i + 2][1]))
                        if block is None:
                            unresolved += 1
                        elif entity.name == "spline":
                            surface_refs += 1
                            surface_hits += surfaceish(block.kind)
                    break  # only the entity's own outermost block
    assert total, "sample has no interned definitions"
    assert unresolved == 0, f"{unresolved} of {total} references do not resolve"
    if surface_refs:
        assert surface_hits == surface_refs, (
            f"{surface_refs - surface_hits} surface references land on a curve"
        )


@pytest.mark.slow
def test_accepted_spline_curves_pass_through_their_vertices(opened):
    """The check that makes a spline curve trustworthy.

    A procedural block can hold several splines, and picking the wrong one
    gives a curve that evaluates cleanly and misses by centimetres.  An edge
    knows two points its curve must contain, so an approximation is only used
    once it has been asked.
    """
    accepted = 0
    worst = 0.0
    for child in opened.documents():
        for body in child.bodies:
            for edge in Shape(body.model()).edges():
                curve = edge.curve
                if not isinstance(curve, SplineCurve):
                    continue
                if usable_curve(edge) is None:
                    continue
                accepted += 1
                for vertex in (edge.start, edge.end):
                    if vertex is None or vertex.position is None or vertex.is_tolerant:
                        continue
                    worst = max(worst, curve.distance_to(vertex.position))
    if not accepted:
        pytest.skip("no evaluable spline curves in this sample")
    assert worst <= SPLINE_FIT_TOLERANCE


def test_spline_curves_cover_most_spline_edges(sucker):
    """Coverage is the point: without this, every spline edge is a gap."""
    total = accepted = 0
    with ezf3d.readfile(sucker) as doc:
        for body in doc.bodies:
            for edge in Shape(body.model()).edges():
                if not isinstance(edge.curve, SplineCurve):
                    continue
                total += 1
                accepted += usable_curve(edge) is not None
    assert total > 100, "this design should be full of spline edges"
    assert accepted / total > 0.85, f"only {accepted}/{total} spline curves usable"


def test_unevaluable_geometry_raises_rather_than_guessing(wheel):
    from ezf3d.asm.geometry import GeometryError

    with ezf3d.readfile(wheel) as doc:
        model = doc.bodies[0].model()
        empty_curve = SplineCurve(entity=model.entities[0], spline=None)
        assert not empty_curve.is_evaluable
        with pytest.raises(GeometryError):
            empty_curve.point_at(0.0)
        empty_surface = SplineSurface(entity=model.entities[0], spline=None)
        assert not empty_surface.is_evaluable
        with pytest.raises(GeometryError):
            empty_surface.point_at(0.0, 0.0)
        assert not empty_surface.fits([np.zeros(3)], 1.0)
