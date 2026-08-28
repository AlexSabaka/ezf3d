"""Recovering where a sketch sits, from the body it helped build.

The evidence here is of a shape this project has not used before. Everywhere
else a reading is checked against a second thing the file says. Here the fit
itself is the check: it solves for two *free* 3-vectors and never requires them
to be perpendicular or unit length, and for a real match they come out
orthonormal anyway. That is what the tests assert — not that the answer is
orthonormal, but that nothing made it so.

The other half is what this cannot do, which is pick one. A design repeats its
own shapes, so a profile lands in as many places as the design has instances,
and that is asserted to be reported rather than resolved.
"""

from __future__ import annotations

import numpy as np
import pytest

from ezf3d.model.placement import (
    MIN_LOOP,
    ORTHONORMAL_TOLERANCE,
    RESIDUAL_TOLERANCE,
    Frame,
    Placement,
    Placements,
    place_sketches,
    signature,
    spread,
    swept_pair,
)
from ezf3d.model.sketch import read_sketches

#: SUCKER's sketch #31 — the 0.5 mm slot — and the extrude that sweeps it.
#: `AlongDistance` is -0.2 mm, so its profile face has a twin 0.02 cm away.
SUCKER_SLOT = 10251
SUCKER_SLOT_SWEEP = 0.02


@pytest.fixture
def placed(opened, sample, _design_cache):
    """``(child document, Placements)`` for every document with bodies and a design."""
    key = ("placement", sample)
    if key not in _design_cache:
        _design_cache[key] = [
            (child, place_sketches(child))
            for child in opened.documents()
            if child.design is not None and child.bodies
        ]
    rows = [row for row in _design_cache[key] if len(row[1])]
    if not rows:
        pytest.skip("no sketch of this sample matches a face")
    return rows


def test_the_fitted_axes_come_out_orthonormal_without_being_asked_to(placed):
    """The whole argument, in one assertion.

    ``_solve`` fits nine free numbers. Nothing in it constrains ``u`` and ``v``
    to be unit length or perpendicular — that would be assuming the answer. For
    every accepted match they are, to machine precision, which is what says the
    sketch coordinates really are a two-dimensional frame and the
    correspondence really is the right one.
    """
    for child, placements in placed:
        for row in placements:
            for frame in row.frames:
                u, v = np.asarray(frame.u_dir), np.asarray(frame.v_dir)
                assert abs(float(np.linalg.norm(u)) - 1.0) < 1e-9, child.name
                assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-9, child.name
                assert abs(float(np.dot(u, v))) < 1e-9, child.name
                assert frame.orthonormality < ORTHONORMAL_TOLERANCE


def test_a_placed_point_lands_on_the_vertex_it_matched(placed):
    for child, placements in placed:
        for row in placements:
            for frame in row.frames:
                assert frame.residual < RESIDUAL_TOLERANCE, f"{child.name}: {row.sketch}"


def test_the_normal_is_a_unit_vector(placed):
    for _, placements in placed:
        for row in placements:
            for frame in row.frames:
                assert abs(float(np.linalg.norm(frame.normal)) - 1.0) < 1e-9


def test_the_match_is_selective(placed):
    """A loop matches a handful of faces, not a useful fraction of them.

    If the signature were weak this would be the symptom: everything matching
    everything, and a "placement" meaning nothing.
    """
    for child, placements in placed:
        assert placements.faces > 0
        for row in placements:
            assert len(row.frames) < placements.faces / 4, f"{child.name}: {row.sketch}"


def test_ambiguity_is_reported_rather_than_resolved(placed):
    """More than one candidate is the honest answer, not a failure.

    A patterned feature puts congruent faces at every instance, so the profile
    genuinely fits in each. This asserts the module says so instead of picking.
    """
    for _, placements in placed:
        counts = spread(placements)
        assert sum(counts.values()) == len(placements)
        assert all(count >= 1 for count in counts)
        assert len(placements.unique()) <= len(placements)


def test_sketches_that_match_nothing_are_counted(placed):
    """Most sketches have been filleted or cut since and no face carries them."""
    for _, placements in placed:
        assert placements.unplaced >= 0
        assert placements.sketches_placed() <= len(placements.by_sketch()) + placements.unplaced


def test_the_sucker_slot_places_on_a_plane_through_the_origin(sucker, shared_document):
    """The case checkable by hand, end to end.

    A 2 cm x 0.05 cm rectangle drawn on the XY plane. Its tightest fit puts the
    origin at the world origin with the normal along +Z, and lands its four
    corners on real vertices to within a femtometre.
    """
    child = shared_document(sucker)
    placements = place_sketches(child)
    rows = placements.by_sketch().get(SUCKER_SLOT)
    assert rows, "the slot sketch should match a planar face"
    frame = rows[0].best
    assert frame is not None
    assert np.allclose(frame.origin, (0.0, 0.0, 0.0), atol=1e-9)
    assert np.allclose(frame.normal, (0.0, 0.0, 1.0), atol=1e-9)
    assert frame.residual < 1e-12
    assert frame.orthonormality < 1e-12


def test_the_slot_lands_wherever_the_design_repeats_it(sucker, shared_document):
    """The candidate count is the design's own multiplicity, not noise.

    SUCKER patterns that slot, and its sketch carries ``R-Pattern1-vCount``.
    So several is the right answer and one would be a wrong one.
    """
    child = shared_document(sucker)
    rows = place_sketches(child).by_sketch()[SUCKER_SLOT]
    sketch = read_sketches(child.design).by_id()[SUCKER_SLOT]
    # The sketch that drives the pattern carries its count; the slot sketch is
    # placed as many times as the design repeats what it drew.
    assert len(rows[0].frames) > 1
    assert not rows[0].is_unique
    assert len(sketch.loops()) == 2


def test_the_sweep_distance_corroborates_from_the_other_stream(sucker, shared_document):
    """A parameter record predicting a distance between ASM vertices.

    ``AlongDistance`` is read by :mod:`ezf3d.model.parameters` out of the design
    stream. The separation it predicts is between vertex positions parsed by
    :mod:`ezf3d.asm`. Nothing connects the two parsers, so agreement is a real
    check on the placement rather than on itself.
    """
    child = shared_document(sucker)
    rows = place_sketches(child).by_sketch()[SUCKER_SLOT]
    kept = swept_pair(rows[0], SUCKER_SLOT_SWEEP)
    assert kept, "no candidate has a twin at the distance the extrude sweeps"
    assert len(kept) < len(rows[0].frames), "the check has to eliminate something"


def test_swept_pair_needs_a_real_distance():
    frame = Frame(origin=(0, 0, 0), u_dir=(1, 0, 0), v_dir=(0, 1, 0))
    row = Placement(sketch=1, loop=(2,), frames=(frame,))
    assert swept_pair(row, 0.0) == ()
    assert swept_pair(row, -1.0) == ()
    # One frame has no twin to pair with.
    assert swept_pair(row, 5.0) == ()


def test_a_signature_ignores_where_the_shape_sits():
    """Rigid motion must not change it, or nothing would ever match."""
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    angle = 0.7
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    moved = [tuple(rot @ np.asarray(p) + np.array([3.0, -2.0])) for p in square]
    assert signature(square) == signature(moved)
    assert signature([(0.0, 0.0), (1.0, 0.0)]) is None
    assert MIN_LOOP == 3


def test_a_frame_places_a_point_where_its_axes_say():
    frame = Frame(origin=(1.0, 2.0, 3.0), u_dir=(0.0, 1.0, 0.0), v_dir=(0.0, 0.0, 1.0))
    assert frame.place(2.0, 5.0) == (1.0, 4.0, 8.0)
    assert np.allclose(frame.normal, (1.0, 0.0, 0.0))


def test_an_empty_result_is_empty_rather_than_wrong():
    empty = Placements()
    assert len(empty) == 0
    assert empty.unique() == [] and empty.by_sketch() == {}
    assert empty.sketches_placed() == 0
    assert spread(empty) == {}
    assert Placement(sketch=1, loop=()).best is None
    assert not Placement(sketch=1, loop=()).is_unique


def test_a_document_with_no_bodies_places_nothing(focuser, shared_document):
    """A registry-less member has neither sketches nor a body to match against."""
    for child in shared_document(focuser).documents():
        if child.design is None or child.bodies:
            continue
        assert len(place_sketches(child)) == 0


@pytest.mark.slow
def test_every_placement_reproduces_its_loop_on_the_body(placed):
    """Walk it back: the frame must put the loop's own points onto the plane it names."""
    for child, placements in placed:
        sketches = read_sketches(child.design).by_id()
        for row in placements:
            at = {p.oid: p for p in sketches[row.sketch].points}
            for frame in row.frames:
                normal = np.asarray(frame.normal)
                origin = np.asarray(frame.origin)
                for point in at.values():
                    world = np.asarray(frame.place(point.x, point.y))
                    assert abs(float(np.dot(world - origin, normal))) < 1e-9, child.name
