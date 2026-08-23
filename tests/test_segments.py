"""Segment streams — headers, slots, and the design/body link."""

from __future__ import annotations

import collections

import ezf3d


def test_segment_slots_match_the_asset_manifest(design):
    """The meta stream and the manifest must agree on which slot a segment is."""
    with ezf3d.readfile(design) as doc:
        for asset in doc.assets.values():
            for name, segment in asset.segments.items():
                declared = next((d for d in asset.manifest.segments if d.matches(name)), None)
                assert declared is not None, name
                assert declared.slot == segment.meta.slot


def test_bulk_stream_version_is_numeric(sample):
    with ezf3d.readfile(sample) as doc:
        for child in doc.documents():
            for segment in child.segments.values():
                assert segment.bulk.version.isdigit()
                assert segment.bulk.body


def test_exactly_one_design_segment_per_asset(design):
    with ezf3d.readfile(design) as doc:
        for asset in doc.assets.values():
            designs = [s for s in asset.segments.values() if s.is_design]
            assert len(designs) == 1, f"{asset.folder}: {[s.name for s in designs]}"


def test_design_stream_references_exactly_the_bodies_on_disk(sample):
    """Cross-validates two independent parsers: the layout scan and the design graph."""
    with ezf3d.readfile(sample) as doc:
        for child in doc.documents():
            for asset in child.assets.values():
                segment = asset.design
                assert segment is not None
                referenced = set(segment.body_refs())
                on_disk = {f"BREP.{b.uuid}.{b.suffix}" for b in asset.bodies}
                assert referenced == on_disk


def test_timeline_vocabulary_is_recovered(bhujha):
    """The design segment names the feature kinds the timeline can use."""
    with ezf3d.readfile(bhujha) as doc:
        features = doc.design.bulk.declared_feature_types()
    for expected in ("ExtrudeFeature", "RevolveFeature", "LoftFeature", "Sketch", "JointOrigin"):
        assert any(name.startswith(expected) for name in features), expected


def test_every_reported_type_name_is_a_real_string(opened_design):
    """The census counts strings, not byte sequences that happen to spell one.

    Run over raw bytes the pattern also matches inside longer strings and
    reports the prefix: SUCKER's 1,118 hits for ``IntrinsicMetaType`` are all
    really ``IntrinsicMetaTypeuint64``, a scalar-type declaration rather than
    a timeline feature.  Anchoring on ``scan_strings`` keeps them whole.
    """
    from ezf3d.streams.primitives import scan_strings

    checked = 0
    for segment in opened_design.segments.values():
        body = segment.bulk.body
        for offset, name in segment.bulk.named_types():
            found = next(iter(scan_strings(body, start=offset, min_len=4)), None)
            assert found is not None and (found.offset, found.value) == (offset, name), (
                f"{name!r} at {offset} is not a string in the stream"
            )
            checked += 1
    assert checked, "no type names in this document"


def test_feature_meta_types_are_a_sorted_registry(opened_design):
    """Which is why a *count* of them says nothing about the design.

    Fusion writes each kind once per registry, alphabetically, and the objects
    index in.  No registry repeats a name, and a name's total across the
    stream is exactly the number of registries that declare it — so
    Robotic_Bhujha's nine ``DcExtrudeFeatureMetaType`` are nine registries
    that permit an extrude, not nine extrudes.
    """
    for segment in opened_design.segments.values():
        registries = segment.bulk.feature_registries()
        if not registries:
            continue
        for block in registries:
            assert block == sorted(block), f"registry out of order: {block}"
            assert len(set(block)) == len(block), f"registry repeats a name: {block}"
        declared = collections.Counter()
        for block in registries:
            declared.update(block)
        census = segment.bulk.type_names()
        for name, count in declared.items():
            assert census[name] == count, name
