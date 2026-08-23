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


def test_the_meta_chain_walks_to_its_declared_length(opened):
    """The header says how many module states follow, and exactly that many do.

    A record is ``str8 previous, str8 identity, u32 kind, str8 owner, u32 n,
    n x u64 ids``.  Getting any field width wrong desynchronises the walk
    immediately, so hitting the declared count on the nose is a strong check
    on the whole shape.
    """
    checked = 0
    for child in opened.documents():
        for name, segment in child.segments.items():
            meta = segment.meta
            assert meta.declared_records > 0, name
            assert len(meta.records) == meta.declared_records, name
            checked += 1
    assert checked, "no segments in this document"


def test_a_meta_record_s_identity_is_its_own_and_its_group_is_shared(opened):
    """Which is what separates the two GUIDs a record carries.

    ``identity`` is unique across a segment's records -- 299 distinct across
    Robotic_Bhujha's 299 -- while ``group`` repeats, 45 distinct over the
    wheel's 167 with one used 22 times.  Reading them the other way round
    would make the list look like a chain, which for the first few records it
    convincingly does and for the whole list does not.
    """
    checked = 0
    for child in opened.documents():
        for name, segment in child.segments.items():
            records = segment.meta.records
            if len(records) < 5:
                continue
            identities = [record.identity for record in records]
            assert len(set(identities)) == len(identities), f"{name}: an identity repeats"
            groups = [record.group for record in records]
            assert len(set(groups)) < len(groups), f"{name}: no group is shared"
            checked += 1
    assert checked, "no segment had enough records to judge"


def test_the_meta_stream_indexes_the_bulk_stream(opened):
    """Object id to byte offset -- and the offsets ascend with the ids.

    That ordering is what makes the bulk stream addressable: consecutive
    entries delimit each object, so a record has a known extent without any
    decoder for its contents.
    """
    checked = 0
    for child in opened.documents():
        for name, segment in child.segments.items():
            index = segment.meta.index
            if not index:
                continue
            checked += 1
            limit = len(segment.bulk.body)
            ids = sorted(index)
            offsets = [index[oid] for oid in ids]
            assert all(offset < limit for offset in offsets), name
            assert offsets == sorted(offsets), f"{name}: offsets do not ascend with ids"
            assert len(set(offsets)) == len(offsets), f"{name}: two objects share an offset"
            # Ids are issued in order and never reused, so the high-water mark
            # sits past the largest one still present.
            assert segment.meta.next_id > ids[-1], name
    assert checked, "no segment in this document carries an index"


def test_indexed_objects_do_not_split_a_string(opened):
    """Independent corroboration that the offsets are record boundaries.

    The type names are found by scanning the bulk stream for length-prefixed
    strings, which knows nothing about the index.  If the offsets were wrong,
    some of those strings would straddle two objects.  None do.
    """
    import bisect

    checked = 0
    for child in opened.documents():
        for name, segment in child.segments.items():
            named = segment.bulk.named_types()
            objects = segment.objects()
            if not named or not objects:
                continue
            starts = [item.offset for item in objects]
            for offset, value in named:
                position = bisect.bisect_right(starts, offset) - 1
                if position < 0:
                    # A segment's index need not begin at offset 0; the
                    # browser's first object starts past its root id string.
                    continue
                end = objects[position].end
                assert offset + 4 + len(value) <= end, f"{name}: {value!r} straddles a boundary"
                checked += 1
    assert checked, "no type names to place"


def test_object_bytes_are_exactly_one_record(wheel):
    with ezf3d.readfile(wheel) as doc:
        segment = doc.design
        objects = segment.objects()
        assert len(objects) > 100
        first = objects[0]
        raw = segment.object_bytes(first.oid)
    assert len(raw) == first.size
    assert segment.object_bytes(10**9) == b""


def test_the_meta_stream_is_read_whole_or_says_what_is_left(opened):
    """Every byte between the index and the footer is accounted for, or counted.

    Zero in the plain documents.  The ``.f3z`` members carry a further section
    holding a wide GUID that this reader does not decode; it is reported as a
    byte count rather than passed over.
    """
    for child in opened.documents():
        for name, segment in child.segments.items():
            meta = segment.meta
            assert meta.unread >= 0
            assert meta.unread < len(meta.body), name
            if meta.schema:
                assert set(meta.schema) == {"Application", "Server"}, name
