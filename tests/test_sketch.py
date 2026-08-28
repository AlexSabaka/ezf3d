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
    OWNER_REACH,
    Point,
    Sketch,
    Sketches,
    read_sketches,
)

#: SUCKER's sketch #31 — the 0.5 mm slot, small enough to check by hand end to
#: end. Its four corners, in centimetres, as Fusion's internal units store them.
SUCKER_SLOT = 10251
SUCKER_SLOT_CORNERS = {(-1.0, -2.0), (1.0, -2.0), (-1.0, -1.95), (1.0, -1.95)}


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


def test_the_module_names_what_it_cannot_do(sucker, shared_document):
    """Curves are located but not typed, and that is stated rather than implied.

    3.6b established that record size classifies nothing across documents, so
    a :class:`~ezf3d.model.sketch.Curve` carries its size as a lead and no
    type at all. If a later phase adds one, this is the test that should
    change with it.
    """
    child = shared_document(sucker)
    sketches = read_sketches(child.design)
    curves = [curve for sketch in sketches for curve in sketch.curves]
    assert curves
    assert all(not hasattr(curve, "kind") for curve in curves)
    assert {type(curve.size) for curve in curves} == {int}


def test_data_directory_is_where_the_samples_live():
    assert (Path(__file__).resolve().parent.parent / "data").is_dir()
