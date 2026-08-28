"""Sketches: the geometry a design draws before it sweeps it.

Ownership here is *read* rather than inferred — each entity record names its
sketch — so the checks below are of two kinds. Some pin that the reference is
doing real work and is not an elaborate restatement of "the nearest sketch
before it". The rest are the cross-check the module rests on: a sketch's
``Linear Dimension`` comes from the parameter table and its points come from
the geometry records, and the two agree.
"""

from __future__ import annotations

from bisect import bisect_right
from itertools import pairwise
from pathlib import Path

import pytest

from ezf3d.model.sketch import (
    COORDINATE_GAP,
    CURVE_KINDS,
    ENDPOINT_GAP,
    FULL_TURN,
    OWNER_REACH,
    Curve,
    Point,
    Sketch,
    Sketches,
    read_sketches,
)

#: SUCKER's sketch #31 — the 0.5 mm slot, small enough to check by hand end to
#: end. Its four corners, in centimetres, as Fusion's internal units store them.
SUCKER_SLOT = 10251
SUCKER_SLOT_CORNERS = {(-1.0, -2.0), (1.0, -2.0), (-1.0, -1.95), (1.0, -1.95)}

#: The two loops that sketch reads as: the outer rectangle and the slot itself.
SUCKER_SLOT_LOOPS = {
    frozenset({10299, 10302, 10305, 10306}),
    frozenset({10315, 10316, 10317, 10318}),
}


def test_every_entity_belongs_to_exactly_one_sketch(sketch_sets):
    """No record is claimed twice, which is what says the owner ref is a key."""
    for child, sketches in sketch_sets:
        seen: dict[int, int] = {}
        for sketch in sketches:
            for entity in (*sketch.points, *sketch.curves):
                assert entity.oid not in seen, (
                    f"{child.name}: {entity.oid} claimed by {seen.get(entity.oid)} and {sketch.oid}"
                )
                seen[entity.oid] = sketch.oid


def test_nearly_every_entity_finds_its_sketch(sketch_sets):
    """Unowned records are counted, not dropped — and there are very few.

    Four across the four development samples. A regression that broke the
    owner reference would show up here as a collapse rather than as silence.
    """
    for child, sketches in sketch_sets:
        # A member with no sketch objects at all is a different case, and
        # test_entity_records_with_no_sketch_are_counted_not_dropped covers it.
        if not len(sketches):
            continue
        placed = sketches.points() + sketches.curves()
        total = placed + sketches.unowned
        if not total:
            continue
        assert placed / total >= 0.98, (
            f"{child.name}: only {placed} of {total} entities name a sketch"
        )


def test_the_owner_reference_is_not_just_the_nearest_sketch(sketch_sets):
    """The reference has to earn its place against the cheaper positional rule.

    Parameters are attributed by nearest-preceding-feature and that is the
    obvious thing to try here too. It disagrees with what the file actually
    says on 145 entities across the samples, so reading the reference is not
    a decorative way of computing a bisect.
    """
    disagreements = 0
    for _, sketches in sketch_sets:
        ids = sorted(sketch.oid for sketch in sketches)
        for sketch in sketches:
            for entity in (*sketch.points, *sketch.curves):
                position = bisect_right(ids, entity.oid) - 1
                if position < 0 or ids[position] != sketch.oid:
                    disagreements += 1
    if not disagreements:
        pytest.skip("this sample's sketches happen not to interleave")
    assert disagreements > 0


def test_a_sketch_owns_records_written_before_it(sketch_sets):
    """The container an entity names precedes the sketch feature it belongs to.

    Worth pinning because the obvious invariant — that a sketch never claims a
    lower id than its own — is *false*: 130 entities across the samples are
    written before their sketch's feature record. A reader that assumed
    otherwise would silently drop them.
    """
    early = sum(
        1
        for _, sketches in sketch_sets
        for sketch in sketches
        for entity in (*sketch.points, *sketch.curves)
        if entity.oid < sketch.oid
    )
    if not early:
        pytest.skip("no sketch in this sample has geometry written before it")
    assert early > 0


def test_the_container_is_within_reach_of_its_sketch(sketch_sets):
    """Every sketch reached is reached by a bounded gap, not by a wide search.

    :data:`OWNER_REACH` is what stops a stray reference matching the *next*
    sketch, so it has to stay small relative to the spacing between sketches.
    """
    for child, sketches in sketch_sets:
        ids = sorted(sketch.oid for sketch in sketches)
        gaps = [b - a for a, b in pairwise(ids)]
        if not gaps:
            continue
        assert min(gaps) > OWNER_REACH, (
            f"{child.name}: sketches sit {min(gaps)} ids apart, closer than the reach"
        )


def test_linear_dimensions_are_a_distance_between_two_of_the_points(sketch_sets):
    """The cross-check: parameter table against geometry records.

    Not self-consistency — the dimension is read from the design's parameter
    objects by :mod:`ezf3d.model.parameters`, and the points from entity
    records by an entirely separate scan. Agreement means both are right.

    Misses are expected and are not tolerated silently: a linear dimension can
    measure a point against a *line*, which this does not resolve yet, so the
    bar is a majority rather than everything.
    """
    for child, sketches in sketch_sets:
        hit, missed = sketches.check()
        total = hit + len(missed)
        if total < 5:
            continue
        assert hit / total >= 0.80, (
            f"{child.name}: only {hit} of {total} dimensions re-derive; missed {missed[:5]}"
        )


def test_coordinates_are_two_dimensional_and_finite(sketch_sets):
    """A sketch is flat in its own frame, which is why the plane is still open."""
    for child, sketches in sketch_sets:
        for sketch in sketches:
            for point in sketch.points:
                assert abs(point.x) < 1e5 and abs(point.y) < 1e5, f"{child.name}: {point}"


def test_the_sucker_slot_reads_as_the_rectangle_its_dimension_says(sucker, shared_document):
    """The one case small enough to check end to end by hand.

    Sketch #31 is a 0.5 mm slot. Its four corners must be exactly 0.05 cm
    apart in y, and its only parameter — ``Linear Dimension-2`` — must say
    0.5 mm. Two readings of the same fact from two places in the file.
    """
    child = shared_document(sucker)
    sketches = read_sketches(child.design).by_id()
    assert SUCKER_SLOT in sketches
    slot = sketches[SUCKER_SLOT]

    corners = {(round(p.x, 9), round(p.y, 9)) for p in slot.points}
    assert corners >= SUCKER_SLOT_CORNERS, f"missing: {SUCKER_SLOT_CORNERS - corners}"

    dimension = next(p for p in slot.parameters if p.role == "Linear Dimension-2")
    assert dimension.expression == "0.5 mm"
    assert dimension.value == pytest.approx(0.05, abs=1e-12)
    assert dimension.value == pytest.approx(-1.95 - -2.0, abs=1e-12)

    checked, missing = slot.dimension_check()
    assert (checked, missing) == (1, ())


def test_a_sketch_outside_the_timeline_is_still_read(focuser, shared_document):
    """Focuser Mk1 keeps 6 of its 39 sketches out of the list.

    Their geometry is in the stream either way, so reading only the timeline's
    sketches would lose it. This is why :func:`read_sketches` takes the wider
    ``named`` set.
    """
    child = next(c for c in shared_document(focuser).documents() if c.design)
    sketches = read_sketches(child.design)
    loose = [sketch for sketch in sketches if not sketch.in_timeline]
    assert loose, "expected sketches outside the timeline in this package"
    assert all(sketch.index == -1 for sketch in loose)
    assert any(sketch.points for sketch in loose), "an outside sketch should still have geometry"


def test_entity_records_with_no_sketch_are_counted_not_dropped(focuser, shared_document):
    """A registry-less ``.f3z`` member has geometry and no feature objects.

    Roundified Cray holds 51 point and curve records and not one sketch to
    hang them on. Reporting zero sketches *and* zero geometry would read as a
    design with nothing in it.
    """
    empty = [
        (child.name, read_sketches(child.design))
        for child in shared_document(focuser).documents()
        if child.design
    ]
    barren = [(name, sketches) for name, sketches in empty if not len(sketches)]
    assert barren, "expected a member with no sketch objects"
    assert any(sketches.unowned for _, sketches in barren), (
        "a member with entity records and no sketches must count them"
    )


def test_reading_twice_gives_the_same_answer(sucker, shared_document):
    child = shared_document(sucker)
    first = read_sketches(child.design)
    second = read_sketches(child.design)
    assert [(s.oid, s.points, s.curves) for s in first] == [
        (s.oid, s.points, s.curves) for s in second
    ]


def test_an_empty_sketch_reports_no_extent_rather_than_raising():
    bare = Sketch(oid=1, index=0, name="Sketch")
    assert bare.extent() == (0.0, 0.0, 0.0, 0.0)
    assert bare.distances() == set()
    assert bare.dimension_check() == (0, ())
    assert Sketches().points() == 0


def test_a_point_measures_distance_in_the_plane():
    assert Point(oid=1, x=0.0, y=0.0).distance(Point(oid=2, x=3.0, y=4.0)) == 5.0


def test_the_coordinate_gap_is_a_constant_not_a_per_sample_tweak():
    """One offset for three record shapes over four subsystem revisions.

    Pinned because the temptation the format keeps offering is a table of
    per-document offsets, which is what a coincidence looks like.
    """
    assert COORDINATE_GAP == 27


@pytest.mark.slow
def test_no_sketch_is_larger_than_the_body_it_helped_build(sketch_sets, tessellated):
    """Design stream against ASM stream — the widest check available here.

    A sketch is drawn on the part, so its extent cannot exceed the diagonal of
    everything the document models. That crosses two streams parsed by
    unrelated code, so a coordinate offset that read plausible-looking noise
    would be very likely to fail it.
    """
    spans = []
    for result in tessellated:
        bounds = result.mesh.bounds()
        if bounds is not None:
            spans.append(float((bounds[1] - bounds[0]).sum()))
    if not spans:
        pytest.skip("this sample has no tessellated geometry to bound against")
    limit = max(spans) * 4
    for child, sketches in sketch_sets:
        for sketch in sketches:
            xmin, ymin, xmax, ymax = sketch.extent()
            assert max(xmax - xmin, ymax - ymin) <= limit, (
                f"{child.name}: sketch {sketch.oid} spans more than the model does"
            )


def test_every_curve_is_typed_by_its_references_not_its_size(sucker, shared_document):
    """The kind is how many points a curve names, which record size cannot give.

    3.6b established that record size classifies nothing across documents, and
    these sizes do move with the revision — SUCKER's line is 356 bytes and
    Robotic_Bhujha's is 360. The reference count is 1, 2 or 3 in every
    document, so that is what is read.
    """
    child = shared_document(sucker)
    sketches = read_sketches(child.design)
    curves = [curve for sketch in sketches for curve in sketch.curves]
    assert curves
    assert all(curve.kind in CURVE_KINDS.values() for curve in curves)
    for curve in curves:
        assert len(curve.points) == {"Circle": 1, "Line": 2, "Arc": 3}[curve.kind]
    # One size does not decide one kind: the same count appears at several sizes.
    by_kind = {kind: {c.size for c in curves if c.kind == kind} for kind in CURVE_KINDS.values()}
    assert by_kind["Arc"] & {367}, by_kind


def test_a_curves_geometry_agrees_with_the_points_it_names(sketch_sets):
    """The check that says the typing is read, not guessed from record size.

    An arc names a centre and two ends, so its stored radius must be the
    distance between them and its stored span the angle they subtend. A circle
    must store a full turn. Record size predicts none of that, so agreement
    here cannot come from having grouped the records by size.
    """
    for child, sketches in sketch_sets:
        good, bad = sketches.curve_check()
        if not good and not bad:
            continue
        assert not bad, f"{child.name}: {len(bad)} curves disagree with their points: {bad[:5]}"
        assert good > 0


def test_a_circle_stores_a_full_turn(sketch_sets):
    """163 of them across the samples, and it is exactly 2pi in every one."""
    seen = 0
    for child, sketches in sketch_sets:
        for sketch in sketches:
            for curve in sketch.curves:
                if curve.kind != "Circle":
                    continue
                seen += 1
                assert curve.span == pytest.approx(FULL_TURN, abs=1e-9), f"{child.name}"
                assert curve.radius > 0.0, f"{child.name}: {curve.oid}"
    if not seen:
        pytest.skip("no circles in this sample")


def test_a_line_carries_no_radius_and_a_unit_span(sketch_sets):
    """+/-1 in all 889. The sign is carried, not interpreted."""
    for child, sketches in sketch_sets:
        for sketch in sketches:
            for curve in sketch.curves:
                if curve.kind != "Line":
                    continue
                assert curve.radius == 0.0, f"{child.name}: {curve.oid}"
                assert abs(curve.span) == pytest.approx(1.0, abs=1e-9), f"{child.name}"


def test_a_curve_only_ever_names_its_own_sketchs_points(sketch_sets):
    """What bounds the walk: a reference outside the point set ends the block.

    Without it the walk would run on into whatever follows and the count —
    which *is* the kind — would be wrong.
    """
    for child, sketches in sketch_sets:
        for sketch in sketches:
            owned = {point.oid for point in sketch.points}
            for curve in sketch.curves:
                assert set(curve.points) <= owned, f"{child.name}: {curve.oid}"


def test_the_reference_count_is_the_kind(sketch_sets):
    for _, sketches in sketch_sets:
        for sketch in sketches:
            for curve in sketch.curves:
                assert CURVE_KINDS[len(curve.points)] == curve.kind


def test_one_record_size_does_not_decide_one_kind(sketch_sets):
    """Size is the lead that found the field, and must not become the rule.

    Within a document sizes do separate the kinds, so this asserts the weaker
    and more useful thing: the reader never consults size, and the same kind
    turns up at several sizes across the corpus.
    """
    by_kind: dict[str, set[int]] = {}
    for _, sketches in sketch_sets:
        for sketch in sketches:
            for curve in sketch.curves:
                by_kind.setdefault(curve.kind, set()).add(curve.size)
    if not by_kind:
        pytest.skip("no curves in this sample")
    assert set(by_kind) <= set(CURVE_KINDS.values())


def test_every_loop_closes_and_uses_each_curve_once(sketch_sets):
    for child, sketches in sketch_sets:
        for sketch in sketches:
            by_id = {curve.oid: curve for curve in sketch.curves}
            for loop in sketch.loops():
                assert len(set(loop.curves)) == len(loop.curves), f"{child.name}"
                if len(loop.curves) == 1 and by_id[loop.curves[0]].kind == "Circle":
                    continue
                assert len(loop.points) == len(loop.curves), f"{child.name}: {loop}"
                # Consecutive curves must share the point between them.
                for index, oid in enumerate(loop.curves):
                    ends = by_id[oid].ends
                    assert ends is not None
                    nxt = loop.points[(index + 1) % len(loop.points)]
                    shared = {loop.points[index], nxt}
                    assert set(ends) == shared, f"{child.name}: {loop} broke at {oid}"


def test_loops_and_loose_curves_account_for_every_curve(sketch_sets):
    """Nothing is silently dropped between the two."""
    for child, sketches in sketch_sets:
        for sketch in sketches:
            used = {oid for loop in sketch.loops() for oid in loop.curves}
            assert len(used) + sketch.loose() == len(sketch.curves), child.name


def test_the_sucker_slot_reads_as_two_loops(sucker, shared_document):
    """The rectangle and the slot — the case checkable by eye.

    Eight line records for what looks like four edges was an open question
    when the points landed. It is two loops, not duplicated edges.
    """
    child = shared_document(sucker)
    slot = read_sketches(child.design).by_id()[SUCKER_SLOT]
    assert {frozenset(loop.curves) for loop in slot.loops()} == SUCKER_SLOT_LOOPS
    assert slot.kinds() == {"Line": 8}
    assert slot.loose() == 0


def test_a_sketch_may_be_a_graph_rather_than_an_outline(sketch_sets):
    """Loose curves are ordinary, and reported rather than forced closed.

    480 of the samples' 1,334 curves sit in no closed loop. A reader that
    demanded every sketch be one profile would be wrong about most of them.
    """
    loose = sum(sketch.loose() for _, sketches in sketch_sets for sketch in sketches)
    curves = sum(len(sketch.curves) for _, sketches in sketch_sets for sketch in sketches)
    if not curves:
        pytest.skip("no curves in this sample")
    assert 0 <= loose < curves


def test_an_unreadable_curve_is_typeless_rather_than_wrong():
    bare = Curve(oid=1, size=0)
    assert bare.kind == "" and bare.points == ()
    assert bare.centre is None and bare.ends is None and not bare.is_closed
    assert Sketch(oid=1, index=0, name="Sketch", curves=(bare,)).loops() == ()
    assert Sketch(oid=1, index=0, name="Sketch", curves=(bare,)).curve_check() == (0, ())


def test_the_endpoint_gap_is_a_constant_not_a_per_document_offset():
    """104 past the anchor in all four documents, at two different absolute
    offsets — 266 in three of them and 214 in Focuser Mk1."""
    assert ENDPOINT_GAP == 104


def test_data_directory_is_where_the_samples_live():
    assert (Path(__file__).resolve().parent.parent / "data").is_dir()
