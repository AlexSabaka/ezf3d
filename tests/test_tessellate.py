"""Face tessellation.

Triangles are checked against the surfaces they came from and against the
topology they must reproduce, not against stored expectations.  Three things
would each pass a naive test while being wrong: triangles that drift off their
surface, a mesh full of cracks, and a volume that does not converge as the
tolerance tightens.
"""

from __future__ import annotations

import numpy as np
import pytest

import ezf3d
from ezf3d.asm.brep import Shape
from ezf3d.asm.geometry import Plane, SplineSurface
from ezf3d.mesh import DEFAULT_CHORD_TOLERANCE, Mesh, tessellate, tessellate_face

TOLERANCE = DEFAULT_CHORD_TOLERANCE


@pytest.mark.slow
def test_triangles_stay_on_their_surface(opened):
    """A triangle spanning too much parameter space cuts the chord.

    Fanning across a cylinder wall instead of walking it looks plausible in a
    render and is out by centimetres, so this is measured per face against the
    surface the face names.  Planes are checked in the same traversal: they
    have no curvature, so their triangles must lie on them exactly.
    """
    curved = over = planar = stray = 0
    for child in opened.documents():
        for body in child.bodies:
            for face in Shape(body.model()).faces():
                surface = face.surface
                if surface is None or isinstance(surface, SplineSurface):
                    continue
                mesh, reason = tessellate_face(face, TOLERANCE)
                if reason is not None or mesh.is_empty:
                    continue
                if isinstance(surface, Plane):
                    planar += 1
                    distances = [
                        surface.distance_to(point) for point in mesh.corners().reshape(-1, 3)
                    ]
                    if float(np.median(distances)) > 1e-6:
                        stray += 1
                    continue
                curved += 1
                centroids = mesh.corners().mean(axis=1)
                deviation = max((surface.distance_to(point) for point in centroids), default=0.0)
                if deviation > TOLERANCE * 2.0:
                    over += 1

    assert curved and planar, "sample produced no analytic faces"
    # A handful of faces are notched regions that are monotone in neither
    # parameter and fall back to a fan: 12 of 6109 cone faces across the
    # samples.  A regression in the stitching would take this into the
    # thousands, which is what the rate is guarding.
    assert over / curved < 0.005, f"{over} of {curved} curved faces exceed twice the tolerance"
    # A rolled-back design leaves faces whose vertices no longer sit on their
    # own plane — 44 of 6884 in the largest sample.  A misread field would miss
    # on every face at once, which is the difference this rate is watching for.
    assert stray / planar < 0.01, f"{stray} of {planar} planar faces are off"


def test_tightening_the_tolerance_refines_the_mesh(wheel):
    with ezf3d.readfile(wheel) as doc:
        shape = Shape(doc.bodies[1].model())
        coarse = tessellate(shape, TOLERANCE)
        fine = tessellate(shape, TOLERANCE / 4.0)
    assert len(fine.mesh) > len(coarse.mesh)
    assert fine.max_deviation < coarse.max_deviation
    # The same faces are built either way; only their density changes.
    assert fine.faces_meshed == coarse.faces_meshed


@pytest.mark.slow
def test_closed_solids_come_out_watertight(tessellated):
    """Most solids that are closed in the B-Rep close in the mesh too.

    Bridging a hole into its outer loop still leaves a few non-manifold edges
    on faces with several holes, so this asserts the rate rather than
    perfection — and the rate is what would collapse if the shared-edge
    guarantee broke.
    """
    watertight = sum(r.watertight_solids for r in tessellated)
    candidates = sum(r.closed_candidates for r in tessellated)
    if candidates == 0:
        pytest.skip("no closed, fully meshed solid in this sample")
    assert watertight / candidates >= 0.6, f"only {watertight}/{candidates} watertight"


def _planar_solid_mesh(solid, tolerance: float) -> Mesh | None:
    """A solid's mesh, but only if every one of its faces is planar."""
    mesh = Mesh()
    for face in solid.faces():
        if not isinstance(face.surface, Plane):
            return None
        part, reason = tessellate_face(face, tolerance)
        if reason is not None:
            return None
        mesh = mesh.merged(part)
    return mesh.welded().cleaned()


def test_an_all_planar_solid_is_tessellated_exactly(wheel):
    """Planes have no curvature, so tolerance cannot change the answer.

    Volume identical at two very different tolerances is a strong statement:
    it means no triangle strayed, none were lost, and the winding is
    consistent enough for the divergence theorem to hold.
    """
    found = 0
    with ezf3d.readfile(wheel) as doc:
        for body in doc.bodies:
            for solid in Shape(body.model()).solids():
                coarse = _planar_solid_mesh(solid, TOLERANCE)
                if coarse is None or coarse.is_empty or not coarse.is_watertight:
                    continue
                fine = _planar_solid_mesh(solid, TOLERANCE / 8.0)
                assert fine is not None
                volume = abs(coarse.volume())
                assert volume > 0.0
                assert abs(abs(fine.volume()) - volume) / volume < 1e-12
                # A closed box cannot enclose more than its bounding box.
                lower, upper = coarse.bounds()
                assert volume <= float(np.prod(upper - lower)) * (1.0 + 1e-9)
                found += 1
    assert found, "expected at least one closed, all-planar solid"


def test_volume_converges_as_the_tolerance_tightens(bhujha):
    """A tessellated curved solid under-reports its volume by the chord error.

    Halving the tolerance must shrink that gap rather than move the answer
    around, which is what catches a triangulation that is merely plausible.
    """
    with ezf3d.readfile(bhujha) as doc:
        body = next(b for b in doc.bodies if b.uuid.startswith("0ee07b70"))
        shape = Shape(body.model())
        coarse = tessellate(shape, 0.05, measure=False).mesh.volume()
        medium = tessellate(shape, 0.01, measure=False).mesh.volume()
        fine = tessellate(shape, 0.002, measure=False).mesh.volume()
    # A chord always falls inside the arc, so refining can only add volume.
    assert coarse < medium < fine, (coarse, medium, fine)
    # And the steps must shrink: a fifth of the tolerance moves the answer by
    # 0.2 %, against 1.4 % for the step before it.
    assert abs(fine - medium) < abs(medium - coarse)
    assert abs(fine - medium) / abs(fine) < 0.005


def test_unsupported_faces_are_named_not_dropped(sucker):
    with ezf3d.readfile(sucker) as doc:
        result = tessellate(Shape(doc.bodies[0].model()), TOLERANCE)
    assert result.faces_skipped > 0, "this design has spline faces"
    assert sum(result.unsupported.values()) == result.faces_skipped
    assert all(reason for reason in result.unsupported)
    assert not result.is_complete


def test_mesh_helpers(wheel):
    with ezf3d.readfile(wheel) as doc:
        mesh = tessellate(Shape(doc.bodies[1].model()), TOLERANCE, measure=False).mesh
    assert mesh.area() > 0
    assert len(mesh.face_normals()) == len(mesh)
    assert np.allclose(np.linalg.norm(mesh.face_normals(), axis=1), 1.0)
    lower, upper = mesh.bounds()
    assert (lower <= upper).all()
    # Welding is idempotent, and cleaning never invents triangles.
    assert len(mesh.welded().triangles) == len(mesh.triangles)
    assert len(mesh.cleaned()) <= len(mesh)
    assert Mesh().is_empty and Mesh().bounds() is None
