"""Analytic curve and surface evaluation.

The load-bearing tests here are not unit tests on synthetic inputs — they
check ezf3d's reading of the format against the format's own internal
redundancy.  ASM stores a curve, an edge's parameter range on that curve, and
the vertex points those parameters must land on.  Evaluating the curve and
comparing to the vertex is a check the file cannot pass by accident.

Two populations are excluded, both for principled reasons:

*Tolerant topology.*  A ``tvertex`` or ``tedge`` is **defined** by not lying
exactly on its curve — that is what the tolerance is for.

*Orphans.*  A design with rollback history leaves stale topology in the main
section.  It resolves cleanly but its vertices no longer match its curves, so
only geometry reachable from a ``body`` is checked.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import ezf3d
from ezf3d.asm.brep import Shape
from ezf3d.asm.geometry import (
    Cone,
    Ellipse,
    GeometryError,
    Plane,
    Sphere,
    SplineCurve,
    SplineSurface,
    Straight,
    Torus,
    read_curve,
    read_surface,
)


def _analytic_edges(model):
    """Reachable edges with ordinary topology and an evaluable curve."""
    for edge in Shape(model).edges():
        if edge.is_tolerant or edge.is_degenerate:
            continue
        curve = edge.curve
        if curve is None or isinstance(curve, SplineCurve):
            continue
        start, end = edge.start, edge.end
        if start is None or end is None or start.is_tolerant or end.is_tolerant:
            continue
        if start.position is None or end.position is None:
            continue
        yield edge, curve, start, end


@pytest.mark.slow
def test_every_vertex_lies_on_its_edge_curve(opened):
    """The headline check for the geometry layer, and it is convention-free.

    Whatever the parameterisation, a vertex must lie *on* the curve its edge
    names.  Reading a field wrongly — a normal taken for a major axis, say —
    moves the curve and this fails everywhere at once.
    """
    worst = 0.0
    checked = 0
    failures = []
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            model = body.model()
            resabs = model.header.resabs
            for edge, curve, start, end in _analytic_edges(model):
                for vertex in (start, end):
                    error = curve.distance_to(vertex.position)
                    worst = max(worst, error)
                    checked += 1
                    if error > resabs:
                        failures.append(
                            f"{body.uuid[:8]} edge#{edge.index} "
                            f"{type(curve).__name__} off by {error:.3e} cm"
                        )
    assert checked, "sample yielded no analytic edges"
    assert not failures, (
        f"{len(failures)} of {checked} endpoints off their curve, "
        f"worst {worst:.3e} cm:\n" + "\n".join(failures[:5])
    )


@pytest.mark.slow
def test_stored_parameters_agree_with_the_vertices_unless_sentinel(opened):
    """Where the stored range is real, ``t -> sense * t`` reproduces the vertex.

    A minority of edges carry a sentinel range instead — the stored numbers do
    not describe the edge's extent — which is why discretisation must invert at
    the vertices rather than trust :attr:`Edge.range`.
    """
    agreed = sentinels = 0
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            model = body.model()
            resabs = model.header.resabs
            for edge, curve, start, end in _analytic_edges(model):
                t0, t1 = edge.range
                errors = [
                    float(np.linalg.norm(curve.point_at(edge.parameter(t)) - v.position))
                    for v, t in ((start, t0), (end, t1))
                ]
                if max(errors) <= resabs:
                    agreed += 1
                else:
                    sentinels += 1
                    assert edge.range_is_sentinel()
                    # The vertices still pin the edge; inversion recovers it.
                    derived = edge.derived_range()
                    assert derived is not None
                    for t, v in zip(derived, (start, end), strict=True):
                        assert float(np.linalg.norm(curve.point_at(t) - v.position)) <= resabs
    total = agreed + sentinels
    assert total, "sample yielded no analytic edges"
    # Sentinels are a small minority; a jump here means the reading regressed.
    assert sentinels / total < 0.05, f"{sentinels}/{total} edges carry sentinel ranges"


def test_reversed_edges_evaluate_at_negated_parameter(opened):
    """A reversed edge runs the curve backwards; ``t -> -t`` is what closes it."""
    proved = False
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            model = body.model()
            resabs = model.header.resabs
            for edge, curve, start, _end in _analytic_edges(model):
                if edge.sense or edge.range_is_sentinel():
                    continue
                t0 = edge.range[0]
                assert edge.parameter(t0) == -t0
                assert float(np.linalg.norm(curve.point_at(-t0) - start.position)) <= resabs
                if float(np.linalg.norm(curve.point_at(t0) - start.position)) > resabs:
                    proved = True  # a case where the sign genuinely matters
    if not proved:
        pytest.skip("no reversed edge in this sample where the sign is decisive")


@pytest.mark.slow
def test_vertices_lie_on_their_face_surface(opened):
    """Each face's vertices must sit on the surface the face names.

    Asserted on the **median** distance per face: a wrong field assignment —
    normal read as u-direction, say — misses on every vertex, while the odd
    stray vertex in a rolled-back design misses on one.
    """
    bad_faces = []
    exact_only = {"sphere": [], "torus": []}
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            model = body.model()
            resabs = model.header.resabs
            for face in Shape(model).faces():
                surface = face.surface
                if surface is None or isinstance(surface, SplineSurface):
                    continue
                name = face.surface_entity.name
                distances = [
                    surface.distance_to(vertex.position)
                    for edge in face.edges()
                    if not edge.is_tolerant
                    for vertex in (edge.start, edge.end)
                    if vertex is not None and vertex.position is not None and not vertex.is_tolerant
                ]
                if not distances:
                    continue
                if name in exact_only:
                    exact_only[name].extend(distances)
                median = float(np.median(distances))
                if median > resabs:
                    bad_faces.append(
                        f"{body.uuid[:8]} face#{face.index} {name} "
                        f"median {median:.3e} cm over {len(distances)} vertices"
                    )
    # A wrong field assignment misses on every face; a rolled-back design
    # leaves a handful of stale faces whose vertices have drifted.
    assert len(bad_faces) <= 30, f"{len(bad_faces)} faces:\n" + "\n".join(bad_faces[:5])
    # Spheres and tori have an exact closed-form distance and no excuse.
    for name, values in exact_only.items():
        if values:
            assert max(values) < 1e-6, f"{name}: worst {max(values):.3e} cm"


def test_elliptical_cones_are_handled(opened):
    """Fusion emits cones with a non-circular cross-section.

    Treating one as circular is wrong by up to a millimetre, which is far
    coarser than the kernel's tolerance and would show up as a lumpy mesh.
    """
    found = False
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            for face in Shape(body.model()).faces():
                surface = face.surface
                if isinstance(surface, Cone) and not surface.is_circular:
                    found = True
                    assert 0.0 < surface.ratio < 1.0
                    # A point placed on the surface must read as on it.
                    for u in (0.0, 1.0, 2.5, math.pi):
                        point = surface.point_at(u, 0.0)
                        assert surface.distance_to(point) < 1e-9
    if not found:
        pytest.skip("no elliptical cones in this sample")


def test_readers_reject_what_they_cannot_evaluate(wheel):
    with ezf3d.readfile(wheel) as doc:
        model = doc.bodies[0].model()
        spline = next(e for e in model.entities if e.name == "spline")
        surface = read_surface(spline)
        assert isinstance(surface, SplineSurface)
        with pytest.raises(GeometryError):
            surface.distance_to(np.zeros(3))
        plane = next(e for e in model.entities if e.name == "plane")
        with pytest.raises(GeometryError):
            read_curve(plane)


def test_geometry_classes_expose_their_shape(wheel):
    with ezf3d.readfile(wheel) as doc:
        model = doc.bodies[0].model()
        by_name = {}
        for entity in model.entities:
            if entity.name not in by_name and entity.base in ("surface", "curve"):
                by_name[entity.name] = entity

    expected = {
        "plane": Plane,
        "cone": Cone,
        "sphere": Sphere,
        "torus": Torus,
        "straight": Straight,
        "ellipse": Ellipse,
    }
    for name, cls in expected.items():
        entity = by_name.get(name)
        if entity is None:
            continue
        read = read_surface if entity.base == "surface" else read_curve
        geometry = read(entity)
        assert isinstance(geometry, cls)

    circle = by_name.get("ellipse")
    if circle is not None:
        ellipse = read_curve(circle)
        # A closed curve returns to its start after a full period.
        assert np.allclose(ellipse.point_at(0.0), ellipse.point_at(2 * math.pi))
        assert ellipse.is_periodic and ellipse.period == pytest.approx(2 * math.pi)
        # Major and minor radii are consistent with the stored ratio.
        assert ellipse.minor_radius == pytest.approx(ellipse.major_radius * ellipse.ratio)
