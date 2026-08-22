"""Edge discretisation.

Polylines are checked against the curve they came from rather than against
stored expectations: every sample must lie on the curve, the ends must land on
the vertices exactly, and the chord sag must respect the tolerance asked for.
"""

from __future__ import annotations

import numpy as np

from ezf3d.asm.brep import Shape
from ezf3d.asm.geometry import SplineCurve
from ezf3d.mesh import DEFAULT_CHORD_TOLERANCE, discretise_edge, wireframe


def _evaluable(shape: Shape):
    for edge in shape.edges():
        curve = edge.curve
        if curve is None or isinstance(curve, SplineCurve) or edge.is_degenerate:
            continue
        if edge.is_tolerant:
            continue
        start, end = edge.start, edge.end
        if start is None or end is None or start.is_tolerant or end.is_tolerant:
            continue
        yield edge, curve


def test_every_sample_lies_on_its_curve(opened):
    worst = 0.0
    checked = 0
    for child in opened.documents():
        for body in child.bodies:
            model = body.model()
            resabs = model.header.resabs
            for edge, curve in _evaluable(Shape(model)):
                line = discretise_edge(edge)
                if line is None:
                    continue
                checked += 1
                worst = max(worst, max(curve.distance_to(point) for point in line))
                assert worst <= resabs, f"{body.uuid[:8]} edge#{edge.index}"
    assert checked


def test_polylines_meet_their_vertices_exactly(opened):
    """Adjacent edges must share an endpoint, or a mesh will not close."""
    checked = 0
    for child in opened.documents():
        for body in child.bodies:
            model = body.model()
            resabs = model.header.resabs
            for edge, _curve in _evaluable(Shape(model)):
                line = discretise_edge(edge)
                ends = edge.endpoints()
                if line is None or ends is None:
                    continue
                checked += 1
                assert np.linalg.norm(line[0] - ends[0]) <= resabs
                assert np.linalg.norm(line[-1] - ends[1]) <= resabs
    assert checked


def test_chord_sag_respects_the_requested_tolerance(opened):
    """Halving the tolerance must actually refine the curve."""
    tolerance = DEFAULT_CHORD_TOLERANCE
    worst = 0.0
    coarse = fine = 0
    for child in opened.documents():
        for body in child.bodies:
            for edge, curve in _evaluable(Shape(body.model())):
                line = discretise_edge(edge, tolerance)
                if line is None or len(line) < 2:
                    continue
                midpoints = (line[:-1] + line[1:]) / 2.0
                worst = max(worst, max(curve.distance_to(point) for point in midpoints))
                coarse += len(line)
                fine += len(discretise_edge(edge, tolerance / 4.0))
    assert worst <= tolerance * 1.001, f"chord sag {worst:.3e} exceeds {tolerance}"
    assert fine > coarse, "a tighter tolerance must add samples"


def test_unevaluable_curves_are_omitted_unless_chords_requested(sucker):
    """A wrong line is worse than a missing one, so chords are opt-in."""
    import ezf3d

    with ezf3d.readfile(sucker) as doc:
        shape = Shape(doc.bodies[0].model())
        without = wireframe(shape)
        with_chords = wireframe(shape, chords=True)
    assert without.omitted > 0, "this design should have spline edges"
    assert with_chords.count == without.count + without.omitted
    assert with_chords.approximated == without.omitted
    assert without.approximated == 0


def test_wireframe_bounds_cover_its_points(opened):
    for child in opened.documents():
        for body in child.bodies:
            frame = wireframe(Shape(body.model()))
            bounds = frame.bounds()
            if bounds is None:
                continue
            points = frame.points()
            assert (points >= bounds[0] - 1e-9).all()
            assert (points <= bounds[1] + 1e-9).all()
            assert len(frame.segments()) >= frame.count - 1
