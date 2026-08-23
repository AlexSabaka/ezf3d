"""The timeline: which features a design has, and in what order.

Order is the one thing here with no internal ground truth — a list is a list,
and nothing in the file says it is *the* timeline. What these check is
everything around it: that the list resolves completely, that its types are
ones the registry declares, that no kind appears more often than the counter
says was ever issued, and that two reads agree. The order itself is for a
human to spot-check against Fusion.
"""

from __future__ import annotations

from collections import Counter

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


# -- 3.6a: the parameters a feature drives ---------------------------------

#: Roles that legitimately belong to several kinds, so a low agreement rate for
#: them is the format and not a misattribution.
SHARED_ROLES = {"countU", "countV", "AlongDistance"}

#: Roles seen fewer times than this are not scored — the rate would be noise.
ROLE_SAMPLE = 10

#: Agreement the positional rule has to reach.  It measures 97.0% today.
ROLE_AGREEMENT = 0.95


def test_a_parameter_never_precedes_the_feature_that_claims_it(timelines):
    """Ownership is positional, so the direction is the whole of the rule."""
    for child, timeline in timelines:
        for feature in timeline:
            for parameter in feature.parameters:
                assert parameter.oid > feature.oid, f"{child.name}: {parameter.name}"


def test_no_parameter_is_claimed_twice(timelines):
    for child, timeline in timelines:
        claimed = [p.oid for f in timeline for p in f.parameters]
        assert len(set(claimed)) == len(claimed), child.name


def test_a_role_names_the_slot_it_fills_in_its_feature(timelines):
    """The check on a positional rule: ``TaperAngle`` has to land on an extrude.

    Roles and kinds are written into different records, so agreement between
    them is evidence rather than a restatement.  Measured per sample, which is
    the stricter reading: 100% on SUCKER, 99.5% on Robotic_Bhujha, 99.7% across
    the package.  The wheel has two parameters and no rate to speak of, so it
    skips rather than passing on nothing.
    """
    seen: Counter[tuple[str, str]] = Counter()
    for _, timeline in timelines:
        for feature in timeline:
            for parameter in feature.parameters:
                seen[parameter.role, feature.kind or "?"] += 1
    if not seen:
        pytest.skip("no attributed parameters in this sample")

    by_role: dict[str, Counter[str]] = {}
    for (role, kind), count in seen.items():
        by_role.setdefault(role, Counter())[kind] += count

    scored = {
        role: kinds
        for role, kinds in by_role.items()
        if sum(kinds.values()) >= ROLE_SAMPLE and role not in SHARED_ROLES
    }
    if not scored:
        pytest.skip("too few parameters in this sample to score")
    agreed = sum(kinds.most_common(1)[0][1] for kinds in scored.values())
    total = sum(sum(kinds.values()) for kinds in scored.values())
    assert agreed / total >= ROLE_AGREEMENT, {
        role: kinds.most_common(2) for role, kinds in scored.items()
    }


def test_the_kinds_that_carry_numbers_get_them(timelines):
    """Coverage per kind, so a regression in the rule shows up as a drop.

    The kinds absent here — PasteBodies, DeleteBody, SplitBody, Combine — carry
    no number at all, which is why overall coverage is 70% and not higher.
    """
    wanted = {
        "ExtrudeFeature": 0.90,
        "FilletEdgeFeature": 0.85,
        "ChamferFeature": 0.85,
        "OffsetFacesFeature": 1.0,
        "CircularPattern": 1.0,
    }
    have: dict[str, list[int]] = {}
    for _, timeline in timelines:
        for feature in timeline:
            if feature.kind not in wanted:
                continue
            counts = have.setdefault(feature.kind, [0, 0])
            counts[1] += 1
            counts[0] += bool(feature.parameters)
    for kind, (driven, total) in have.items():
        assert driven / total >= wanted[kind], f"{kind}: {driven}/{total}"


def test_a_two_sided_extrude_keeps_both_sides(bhujha, shared_document):
    """Robotic_Bhujha runs one, and its second side is only in the role names.

    ``AgainstDistance`` and ``Side2TaperAngle`` beside the usual pair is how a
    symmetric extrude is visible at all — nothing else in the record says so.
    """
    from ezf3d.model.parameters import read_parameters

    segment = shared_document(bhujha).design
    timeline = read_timeline(segment, read_parameters(segment))
    two_sided = [f for f in timeline if f.role("AgainstDistance") is not None]
    assert two_sided, "expected a two-sided extrude"
    for feature in two_sided:
        assert feature.kind == "ExtrudeFeature"
        assert feature.role("Side2TaperAngle") is not None
        assert feature.role("AlongDistance") is not None


def test_the_wheels_one_extrude_reads_end_to_end(wheel, shared_document):
    """Small enough to check by hand, so it is checked by hand."""
    from ezf3d.model.parameters import read_parameters

    segment = shared_document(wheel).design
    timeline = read_timeline(segment, read_parameters(segment))
    extrude = next(f for f in timeline if f.kind == "ExtrudeFeature")
    assert extrude.role("AlongDistance").expression == "-1 mm"
    assert extrude.role("AlongDistance").display == pytest.approx(-1.0)
    assert extrude.role("TaperAngle").expression == "0.0 deg"


def test_the_join_is_opt_in(wheel, shared_document):
    """Reading the order must not cost a second pass over the stream."""
    segment = shared_document(wheel).design
    assert all(not feature.parameters for feature in read_timeline(segment))
