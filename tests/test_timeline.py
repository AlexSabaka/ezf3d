"""The timeline: which features a design has, and in what order.

Order is the one thing here with no internal ground truth — a list is a list,
and nothing in the file says it is *the* timeline. What these check is
everything around it: that the list resolves completely, that its types are
ones the registry declares, that no kind appears more often than the counter
says was ever issued, and that two reads agree. The order itself is for a
human to spot-check against Fusion.
"""

from __future__ import annotations

import pytest

from ezf3d.model.timeline import KIND_ALIASES, kind_of, read_feature, read_timeline


def test_every_entry_resolves_to_a_feature(timelines):
    """The list is accepted only when every entry resolves, so this pins it.

    A timeline item carries no pointer back to its feature — the feature is
    the object before it in the index — and that is exactly what makes the
    list identifiable among every other reference list in the stream.
    """
    for child, timeline in timelines:
        if not timeline.features:
            continue
        body = child.design.bulk.body
        by_id = {item.oid: item for item in child.design.objects()}
        for feature in timeline:
            assert read_feature(body, by_id[feature.oid]) is not None, feature.oid
            assert feature.item in by_id, feature.oid


def test_positions_are_dense_and_ordered(timelines):
    for child, timeline in timelines:
        assert [f.index for f in timeline] == list(range(len(timeline))), child.name
        oids = [f.oid for f in timeline]
        assert len(set(oids)) == len(oids), f"{child.name}: a feature is listed twice"


def test_every_kind_is_one_the_registry_declares(timelines):
    """Two things written separately agreeing, not a restatement.

    The registry is a table of names near the top of the stream; the timeline
    is a list of object ids elsewhere in it.  That every label in the second
    names an entry in the first is a real check on the reading — and on
    :data:`KIND_ALIASES`, which is where Fusion's UI and its serializer
    disagree about a name.
    """
    for child, timeline in timelines:
        unknown, _ = timeline.check()
        assert not unknown, f"{child.name}: {unknown}"


def test_no_kind_outruns_its_counter(timelines):
    """The registry counts what was ever issued, so the live timeline cannot exceed it.

    This is the sharper of the two checks: Fusion counts its own labels up with
    that number, so a list holding more extrudes than were ever issued would
    not be a timeline at all.
    """
    for child, timeline in timelines:
        _, over = timeline.check()
        assert not over, f"{child.name}: {over}"


def test_the_counter_is_an_upper_bound_not_a_census(timelines):
    """SUCKER declares 83 and runs 58 — deletions are why the two differ."""
    for child, timeline in timelines:
        if not timeline.features:
            continue
        issued = sum(timeline.declared.values())
        assert issued >= len(timeline.census()), child.name
        for kind, live in timeline.census().items():
            assert live <= timeline.declared[kind], f"{child.name}: {kind}"


def test_reading_twice_gives_the_same_order(design, shared_document):
    segment = shared_document(design).design
    first = [(f.index, f.oid, f.name) for f in read_timeline(segment)]
    second = [(f.index, f.oid, f.name) for f in read_timeline(segment)]
    assert first == second
    assert first, "expected a timeline in this sample"


def test_timeline_order_is_not_object_id_order(sucker, shared_document):
    """SUCKER's ninth entry was created after its fiftieth.

    This is the reason the list is read rather than reconstructed from ids:
    ordering by creation would put ``Mirror`` (12657) last instead of tenth.
    """
    timeline = read_timeline(shared_document(sucker).design)
    oids = [feature.oid for feature in timeline]
    assert oids != sorted(oids)
    mirror = next(f for f in timeline if f.name == "Mirror")
    assert mirror.index == 9
    assert mirror.oid > max(f.oid for f in timeline if f.index < 9)


def test_a_design_with_no_feature_registry_has_no_timeline(focuser, shared_document):
    """Two package members hold imported bodies and declare no feature kind.

    They do hold objects shaped like a feature — a guid, no inputs, no name —
    and an earlier reading turned seven body-appearance records into a
    seven-entry timeline.  Requiring every entry to resolve through the object
    *before* it is what rejects them.
    """
    empty = [
        child
        for child in shared_document(focuser).documents()
        if child.design and not child.design.bulk.feature_counters()
    ]
    assert empty, "expected a member with no feature registry"
    for child in empty:
        timeline = read_timeline(child.design)
        assert timeline.oid == 0 and not timeline.features, child.name


def test_features_outside_the_list_are_counted_not_dropped(bhujha, shared_document):
    """Robotic_Bhujha's joints are not in its list, and that is said out loud."""
    timeline = read_timeline(shared_document(bhujha).design)
    assert timeline.outside, "expected assembly features outside the list"
    assert "JointOriginFeature" in timeline.outside
    assert sum(timeline.outside.values()) + len(timeline) <= sum(timeline.declared.values())


def test_kind_of_prefers_the_alias_then_the_plain_name():
    declared = {"ExtrudeFeature": 1, "DeleteBody": 1, "Sketch": 1, "BaseFeature": 1}
    assert kind_of("Extrude", declared) == "ExtrudeFeature"
    assert kind_of("RemoveBody", declared) == "DeleteBody"
    assert kind_of("Sketch", declared) == "Sketch"
    assert kind_of("Base Feature", declared) == "BaseFeature"
    assert kind_of("Nonesuch", declared) == ""
    assert kind_of("", declared) == ""


def test_every_label_used_by_a_sample_resolves(timelines):
    """No label falls through to an empty kind — the aliases cover what is used."""
    for child, timeline in timelines:
        for feature in timeline:
            if feature.name:
                assert feature.kind, f"{child.name}: {feature.name!r} names no kind"


@pytest.mark.parametrize("label", sorted(KIND_ALIASES))
def test_every_alias_does_work_the_plain_rules_cannot(label):
    """A redundant alias is a guess nothing pins, so the table stays minimal.

    Three entries were dropped when this test first ran: ``JointOrigin`` and
    ``MotionLink`` and ``ReparentDecal`` are already reached by appending
    ``Feature``.
    """
    bare = label.replace(" ", "")
    assert KIND_ALIASES[label] not in (label, bare, f"{bare}Feature")
