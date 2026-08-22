"""Segment streams — headers, slots, and the design/body link."""

from __future__ import annotations

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
    """The design segment names the feature kinds the timeline uses."""
    with ezf3d.readfile(bhujha) as doc:
        features = doc.design.bulk.feature_types()
    for expected in ("ExtrudeFeature", "RevolveFeature", "LoftFeature", "Sketch", "JointOrigin"):
        assert any(name.startswith(expected) for name in features), expected
