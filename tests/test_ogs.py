"""Fusion's graphics cache, and what it proves about everything else.

The cache is a second, independent account of the same solid: Autodesk's
tessellator against ezf3d's reading of the surface equations.  That makes it
worth more as *evidence* than as geometry, and most of these tests use it that
way — the cache is where the spline-surface gap in :mod:`ezf3d.asm.geometry`
stopped being a suspicion and became a measurement.

Three properties are checked without reference to anything stored: the
descriptors tile the vertex blob exactly, every buffer lies inside the box its
own scene node declares, and the cached vertices sit on the analytic surfaces
the B-Rep names.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ezf3d.asm.brep import Shape
from ezf3d.mesh import tessellate
from ezf3d.ogs import GraphicsCache, compare, hausdorff
from ezf3d.ogs.stream import MAGIC, class_census, read_wstr, walk
from ezf3d.render.scene import build_cached, open_cache

#: Kinds whose reading is settled.  Splines are the exception and have their
#: own test, which is the point of this module.
ANALYTIC = ("Plane", "Cone", "Sphere", "Torus")


@pytest.fixture
def cache(opened) -> GraphicsCache:
    found = open_cache(opened)
    if found is None:
        pytest.skip("this sample carries no graphics cache")
    return found


def test_a_world_opens_the_way_it_always_does(cache, opened):
    for child in opened.documents():
        asset = child.primary
        if asset is None or not asset.layout.ogs:
            continue
        world = next(name for name in asset.layout.ogs if name.endswith("/world"))
        data = asset._archive.read(world)
        assert read_wstr(data, 0)[0] == MAGIC
        census = class_census(data)
        # The scene graph is nodes named after B-Rep entities, not an opaque blob.
        assert census["Face"] and census["Edge"]
        assert sum(census.values()) == sum(1 for _ in walk(data))


def test_descriptors_tile_the_vertex_blob_exactly(cache):
    """The sharpest check on the walk, and the reason to trust the heuristic.

    A missed scene node leaves a gap in the blob and an invented one an
    overlap.  Neither happens, in either sample that has a cache — so every
    byte of vertex data is accounted for by a node that claims it.
    """
    gap, overlap = cache.coverage()
    assert (gap, overlap) == (0, 0)
    assert cache.world.buffers
    # Nodes that carry the flag word but no readable descriptor are counted,
    # and the exact coverage above is what says they were not geometry: there
    # is no room left in the blob for them to have claimed.
    assert cache.world.unread < len(cache.world.buffers)


def test_every_buffer_sits_inside_the_box_its_node_declares(cache):
    """Each scene node states a bounding box; the geometry must honour it."""
    checked = 0
    for face, buffer in zip(cache.faces(), cache.world.faces, strict=True):
        if buffer.box is None:
            continue
        lower, upper = buffer.box
        assert (face.points >= lower - 1e-4).all()
        assert (face.points <= upper + 1e-4).all()
        checked += 1
    assert checked, "no face declared a box"


def test_cached_faces_are_well_formed(cache):
    for face in cache.faces():
        assert len(face.triangles), "a cached face with no triangles"
        assert face.triangles.max() < len(face.points)
        assert face.triangles.min() >= 0
        lengths = np.linalg.norm(face.normals, axis=1)
        assert np.allclose(lengths, 1.0, atol=1e-5)


def test_cached_mesh_is_wound_outwards(cache):
    """Positive volume by the divergence theorem means outward-facing."""
    mesh = cache.mesh()
    assert not mesh.is_empty
    assert mesh.volume() > 0.0


def test_cache_welds_into_a_closed_surface(cache):
    """Fusion's own tessellation is conformal: shared edges share vertices.

    Not every sample is perfectly manifold — one has a single edge used by
    four triangles, a pinch in Fusion's own cache — so the check is that
    boundary edges do not exist rather than that nothing is unusual.  A
    boundary edge would mean a crack, which is what would break an export.
    """
    counts = cache.mesh().edge_use_counts()
    assert counts.get(1, 0) == 0, f"cached mesh has {counts.get(1)} boundary edges"
    assert counts.get(2, 0) > 0


@pytest.fixture(scope="session")
def _agreement_cache():
    """Cache/B-Rep comparisons, computed once per sample.

    Pairing every cached face with a B-Rep face and measuring it against that
    face's surface is the expensive part of this module — a minute and a
    quarter on SUCKER.  Two tests read the same result.
    """
    return {}


@pytest.fixture
def agreement(opened, cache, sample: Path, _agreement_cache):
    """How closely the cache and the B-Rep agree, for the current sample.

    Skips when no single body owns the cached faces: an assembly's cache is
    drawn from many bodies at once, and there is nothing to compare it
    against one at a time.
    """
    if sample not in _agreement_cache:
        found = build_cached(opened, identify=True)
        best = None
        for child in opened.documents():
            for body in child.bodies:
                if body.uuid not in found.candidates:
                    continue
                report = compare(cache, Shape(body.model()))
                if best is None or report.matched > best.matched:
                    best = report
        _agreement_cache[sample] = best
    report = _agreement_cache[sample]
    if report is None:
        pytest.skip("no single body owns these cached faces")
    assert report.matched, "no cached face could be paired with a B-Rep face"
    return report


@pytest.mark.slow
def test_analytic_surfaces_agree_with_fusions_own_tessellation(agreement):
    """Cached vertices lie on the planes, cones, spheres and tori ezf3d reads.

    This is the strongest evidence the analytic geometry is read correctly,
    because it goes nowhere near ezf3d's tessellator: it takes Autodesk's
    points and asks the surface equations where they should be.  Agreement is
    at float32 noise — the cache stores single precision — which is as close
    as two independent computations of the same surface can come.
    """
    report = agreement
    seen = 0
    for kind in ANALYTIC:
        if not report.by_surface.get(kind):
            continue
        seen += 1
        assert report.typical(kind) < 1e-5, f"{kind} typically {report.typical(kind):.2e} cm out"
    assert seen >= 2, "sample exercised too few analytic surface kinds"


@pytest.mark.slow
def test_spline_surfaces_are_the_one_kind_that_disagrees(agreement):
    """The open item, stated as a measurement rather than a worry.

    ezf3d can read a ``nubs`` surface and evaluate it exactly; what it cannot
    yet establish is *which* of a procedural block's nested approximations
    belongs to a given face.  Fusion's own vertices say the one currently
    picked is not it — by three orders of magnitude more than any analytic
    surface misses by.  That is why ``TESSELLATE_SPLINE_SURFACES`` is off.

    When the identification is solved this test should fail, and the right
    response is to delete it.
    """
    report = agreement
    if not report.by_surface.get("SplineSurface"):
        pytest.skip("no spline surfaces paired in this sample")
    analytic = max(
        (report.typical(kind) for kind in ANALYTIC if report.by_surface.get(kind)),
        default=0.0,
    )
    assert report.typical("SplineSurface") > 100 * max(analytic, 1e-9)


def test_every_cached_corner_is_a_brep_vertex(opened, cache):
    """The strongest single statement about the decode, and it is exact.

    Cached edges are polylines between B-Rep vertices.  If the scene graph
    were being misread — wrong stride, wrong offset, wrong blob — the
    endpoints would be arbitrary points and none of them would coincide with
    a ``point`` record.  Every one of them does, in every sample, to within a
    micron: 100.0 %.  It also fixes the units and the frame, since a
    coordinate system that were not shared could not produce that.
    """
    found = build_cached(opened, identify=True)
    assert found is not None
    assert found.corner_coverage == pytest.approx(1.0)
    assert found.contributors >= 1


def test_an_assembly_cache_belongs_to_no_single_body(focuser, shared_document):
    """The ``.f3z`` sample caches ten bodies at once, and declines to pick one.

    Its corners still all land on B-Rep vertices, so the cache is read
    correctly; it simply draws more than one body, which is a reason to
    tessellate rather than to trust it as one solid.
    """
    document = shared_document(focuser)
    found = build_cached(document, identify=True)
    assert found is not None
    assert found.contributors > 1
    assert found.body is None
    assert not found.covers_body
    assert found.corner_coverage == pytest.approx(1.0)


def test_a_design_without_a_cache_is_not_an_error(bhujha, shared_document):
    document = shared_document(bhujha)
    assert build_cached(document) is None


def test_the_cache_is_identified_with_a_body_by_its_corners(wheel, shared_document):
    """Cached edge endpoints are B-Rep vertices, which names the body.

    The wheel is the clean case: one body's points account for every cached
    corner and the other body's for none.
    """
    document = shared_document(wheel)
    found = build_cached(document, identify=True)
    assert found is not None
    assert found.body is not None
    assert found.candidates == [found.body]
    assert found.faces == found.body_faces
    assert found.covers_body


def test_a_partial_cache_is_reported_as_partial(sucker, shared_document):
    """SUCKER's cache holds 608 faces of a body with 2006, and says so.

    Using it as if it were the whole solid would quietly export a third of a
    part, so ``--source auto`` must decline it.
    """
    document = shared_document(sucker)
    found = build_cached(document, identify=True)
    assert found is not None
    assert found.faces < found.body_faces
    assert not found.covers_body


@pytest.mark.slow
def test_every_vertex_ezf3d_produces_lies_on_fusions_own_surface(wheel, shared_document):
    """One-sided Hausdorff from ezf3d's mesh vertices to Fusion's triangles.

    This direction needs no face pairing, which is what makes it a fair test:
    every face ezf3d meshes is also in the cache, so each of its vertices must
    land on the cached surface.  The reverse is not true — the cache covers
    spline faces ezf3d declines to mesh — so measuring that way would just
    re-measure the known gap.

    A vertex of ezf3d's mesh sits on the true surface, and the cache
    approximates that surface with chords, so the distance is bounded by
    Fusion's own sagitta rather than by anything ezf3d chose: 1.7e-07 cm at
    the median and 9.5e-03 cm at worst across the wheel.
    """
    document = shared_document(wheel)
    cache = open_cache(document)
    body = next(
        item
        for child in document.documents()
        for item in child.bodies
        if item.uuid.startswith("068db28d")
    )
    built = tessellate(Shape(body.model()), 0.02, measure=False).mesh
    assert len(built) > 1000, "nothing was tessellated to compare"
    points = built.vertices
    step = max(1, len(points) // 2000)
    worst = hausdorff(points[::step], cache.mesh())
    assert worst < 0.02, f"{worst:.5f} cm from Fusion's own surface"


def test_tessellating_a_cached_body_covers_the_same_ground(wheel, shared_document):
    """A sanity check that the two paths describe one object, not two.

    Bounds rather than triangles: the cache and the tessellation disagree
    about how finely to cut a curve, and about spline surfaces, but they
    cannot disagree about where the body is.
    """
    document = shared_document(wheel)
    cached = build_cached(document, identify=False)
    body = next(
        item
        for child in document.documents()
        for item in child.bodies
        if item.uuid.startswith("068db28d")
    )
    built = tessellate(Shape(body.model()), measure=False).mesh
    lower, upper = cached.mesh.bounds()
    other_lower, other_upper = built.bounds()
    # The tessellation omits spline faces, so it may be smaller — never larger.
    assert (other_lower >= lower - 1e-3).all()
    assert (other_upper <= upper + 1e-3).all()
    assert float(np.max(upper - lower)) == pytest.approx(
        float(np.max(other_upper - other_lower)), rel=0.05
    )
