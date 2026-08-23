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
def test_triangles_stay_on_their_surface(meshed_faces):
    """A triangle spanning too much parameter space cuts the chord.

    Fanning across a cylinder wall instead of walking it looks plausible in a
    render and is out by centimetres, so this is measured per face against the
    surface the face names.  Planes are checked in the same traversal: they
    have no curvature, so their triangles must lie on them exactly.
    """
    curved = over = planar = stray = 0
    worst = 0.0
    for face, mesh, reason, _ in meshed_faces:
        surface = face.surface
        if surface is None or isinstance(surface, SplineSurface):
            continue
        if reason is not None or mesh.is_empty:
            continue
        if isinstance(surface, Plane):
            planar += 1
            distances = [surface.distance_to(point) for point in mesh.corners().reshape(-1, 3)]
            if float(np.median(distances)) > 1e-6:
                stray += 1
            continue
        curved += 1
        centroids = mesh.corners().mean(axis=1)
        deviation = max((surface.distance_to(point) for point in centroids), default=0.0)
        if deviation > TOLERANCE * 2.0:
            over += 1

    assert curved and planar, "sample produced no analytic faces"
    # Hard guarantee: a face whose triangulation strays four times past the
    # tolerance is reported instead of meshed, so nothing that made it into
    # the mesh may exceed that.
    assert worst <= TOLERANCE * 4.0, f"a meshed face deviates by {worst:.3e} cm"
    # Softer: a few faces are notched regions, monotone in neither parameter,
    # that fall back to a fan and land between two and four times over.  A
    # regression in the stitching would take this from a few percent to most.
    assert over / curved < 0.08, f"{over} of {curved} curved faces exceed twice the tolerance"
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
    # Fewer faces survive at a tighter tolerance, not more: the bar a face has
    # to clear to be meshed at all is set relative to the tolerance asked for.
    assert fine.faces_meshed <= coarse.faces_meshed


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
        part, reason, _deviation = tessellate_face(face, tolerance)
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


@pytest.mark.slow
def test_every_vertex_of_a_meshed_face_lies_on_its_surface(meshed_faces):
    """A loop reached from a face need not be a loop *of* that face.

    A ``next`` chain in a rolled-back design can run into a loop bounding a
    different face, and the loop's own ``face`` pointer does not settle it:
    two faces can reach one record and it names only one of them.  So the
    tessellator asks the geometry, and this checks that it did.

    Asking the mesh rather than re-walking the loops is both cheaper and
    stronger.  Cheaper because the mesh is already in hand; stronger because
    tessellation adds no Steiner points, so every vertex either came from a
    loop polyline or was computed on the surface — and both must be on it.
    One outline in the samples sits 2.9 cm out, which is what this rejects.
    """
    checked = 0
    for face, mesh, reason, _ in meshed_faces:
        surface = face.surface
        if surface is None or isinstance(surface, SplineSurface):
            continue
        if reason is not None or mesh.is_empty:
            continue
        worst = max(abs(surface.distance_to(point)) for point in mesh.vertices)
        assert worst <= max(TOLERANCE, 1e-4), (
            f"a vertex of face#{face.index} sits {worst:.3e} cm off its surface"
        )
        checked += 1
    assert checked, "no analytic face was meshed"


@pytest.mark.slow
def test_triangulation_respects_a_face_s_holes(meshed_faces):
    """A meshed face must cover its outer loop less its holes — exactly.

    Nothing else in this suite can see a hole being filled in.  Filled-in
    triangles sit on the plane like any other, so the deviation check passes;
    they close the surface, so watertightness passes; and their count is the
    same, so a triangle tally passes.  What gives it away is area, and it gave
    it away only once Fusion's own cached mesh showed three sectors of a
    handwheel triangulated as a solid disc.

    The cause was a bridged hole: splicing one into its outer loop repeats two
    vertices, an ear test that went by index found those duplicates on every
    candidate ear, and the clipper stalled and fanned.  The wheel's surface
    area came out at 2003 cm2 where its meshable faces come to 718.
    """
    from ezf3d.mesh.tessellate import _signed_area, _to_uv, loop_polyline

    checked = 0
    for face, mesh, reason, _ in meshed_faces:
        loops = list(face.loops())
        # Only a plane's parameter-space area is its real area.
        if len(loops) < 2 or not isinstance(face.surface, Plane):
            continue
        if reason is not None or mesh.is_empty:
            continue
        rings = [loop_polyline(loop, TOLERANCE) for loop in loops]
        if any(ring is None for ring in rings):
            continue
        wanted = abs(sum(_signed_area(_to_uv(face.surface, ring)) for ring in rings))
        # The comparison is 3D area against parameter-space area, and a loop
        # can sit a fraction of a micron off its own plane — 1.0e-05 cm at
        # worst in these samples, for 1.6e-06 of relative area.  The failure
        # this guards against was 3600 %.
        assert mesh.area() == pytest.approx(wanted, rel=1e-4), (
            f"{len(loops)}-loop face covers {mesh.area():.4f} where its loops enclose {wanted:.4f}"
        )
        checked += 1
    assert checked, "no multi-loop planar face in this sample"


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
