"""Readers for a segment's ``MetaStream.dat`` / ``BulkStream.dat`` pair.

A *segment* is one subsystem's slice of the document — the design timeline, the
browser tree, the assembly-context tree.  Each is stored as two files:

``MetaStream.dat``
    The index: segment type, the GUIDs of the schema modules that wrote it, and
    per-module records naming the object ids present in the bulk stream.

``BulkStream.dat``
    The payload: a typed object graph, **uncompressed**, whose records carry
    readable meta-type names (``DcExtrudeFeatureMetaType``, ``SketchesRoot``,
    ...).

**Those names are a dictionary, not a timeline.**  Fusion writes each kind once,
sorted by name, and the objects index into that — so a design that declares
``DcExtrudeFeatureMetaType`` has *some* number of extrudes, and the stream does
not say how many at that point.  Counting declarations and calling the result a
feature census is the mistake this module used to make; see
:meth:`BulkStream.feature_registries`.

Headers are decoded here.  The per-record bodies need a schema-versioned
decoder; until then :attr:`Segment.body` keeps the undecoded bytes reachable.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ezf3d.streams.primitives import Reader, StreamError, scan_strings

#: Same sentinel as the document manifest; absent in older revisions, so the
#: version block is located by validating what follows it.
META_COOKIE = 1234

#: Candidate widths, in words, of the version block before the type/owner pair.
_VERSION_BLOCK_WORDS = (4, 2, 6)

#: Type names Fusion uses in the bulk stream.  ``Dc*MetaType`` are timeline
#: features and sketches, ``*Root`` are the containers that own them, and
#: ``IntrinsicMetaType*`` declares a scalar type such as ``uint64``.
#:
#: Matched against **decoded strings**, not raw bytes, and matched whole.  Run
#: over raw bytes it truncates: every one of SUCKER's 1,118 hits for
#: ``IntrinsicMetaType`` is really ``IntrinsicMetaTypeuint64``, and reporting
#: the prefix turned a scalar-type declaration into a phantom timeline feature.
#: The trailing ``[A-Za-z0-9_]*`` is what keeps those names whole.
TYPE_NAME_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_]{3,60}(?:MetaType|Root|Manager|Attributes)[A-Za-z0-9_]*"
)

#: Prefix Fusion gives a timeline feature's meta-type.  ``PassiveRefMetaType``,
#: ``StrongRefMetaType`` and the intrinsics also end in ``MetaType`` and are not
#: features, so the prefix is what selects rather than the suffix.
FEATURE_PREFIX = "Dc"
FEATURE_SUFFIX = "MetaType"

#: How the design graph names a B-Rep body: by its blob filename.  Current
#: documents write it as UTF-16LE, older ones as ASCII, so both are searched.
BREP_REF_RE = re.compile(rb"BREP\.[0-9a-fA-F-]{36}\.smbh?")
BREP_REF_WIDE_RE = re.compile(
    rb"B\x00R\x00E\x00P\x00\.\x00(?:[0-9a-fA-F-]\x00){36}\.\x00s\x00m\x00b\x00(?:h\x00)?"
)


@dataclass(slots=True)
class MetaStream:
    """Parsed header of a ``MetaStream.dat``."""

    #: Folder prefix this segment was written under -- ``Design``, ``ACT``, or
    #: the full type name.  Matches the asset manifest's declaration prefix.
    prefix: str
    #: Slot id from the asset manifest's segment table.
    slot: int
    #: Identity GUID; all-zero for a segment that has never been branched.
    guid: str
    reserved: tuple[int, ...] = ()
    #: Fully-qualified segment type, e.g. ``FusionDesignSegmentType``.
    declared_type: str = ""
    #: Owning subsystem, e.g. ``Fusion``.
    owner: str = ""
    #: Offset at which the header ends and per-module records begin.
    body_offset: int = 0
    body: bytes = b""


@dataclass(slots=True)
class BulkStream:
    """Parsed header of a ``BulkStream.dat``."""

    #: Schema revision as Fusion writes it — a numeric string such as ``"397"``.
    version: str
    #: Zero in most streams, 2 in browser streams; role not yet established.
    flags: int = 0
    body_offset: int = 0
    body: bytes = b""

    def named_types(self) -> list[tuple[int, str]]:
        """``(offset, name)`` for every type name in the payload, in file order.

        Anchored on :func:`scan_strings`, so every entry is a real
        length-prefixed string at the offset given rather than a byte sequence
        that happens to spell one.
        """
        return [
            (found.offset, found.value)
            for found in scan_strings(self.body, min_len=4)
            if TYPE_NAME_RE.fullmatch(found.value)
        ]

    def type_names(self) -> Counter[str]:
        """Census of the type names the payload declares."""
        return Counter(name for _, name in self.named_types())

    def feature_registries(self) -> list[list[str]]:
        """The feature dictionaries, in file order, each one sorted by name.

        Fusion writes the timeline's meta-types as a registry — one entry per
        kind, alphabetical, that the objects index into.  A design can hold
        several: Robotic_Bhujha has eleven, of 2 to 14 entries each.

        Two measurements say a *count* of these names means nothing.  No
        registry repeats a name, and every name's total across the stream
        equals the number of registries that declare it — so Robotic_Bhujha's
        nine ``DcExtrudeFeatureMetaType`` are nine registries that each allow
        an extrude, not nine extrudes.  That number is the one this project's
        own README used to advertise as a feature census.
        """
        blocks: list[list[str]] = []
        current: list[str] = []
        for _, name in self.named_types():
            if is_feature_type(name):
                current.append(name)
                continue
            if current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        return blocks

    def declared_feature_types(self) -> set[str]:
        """The timeline feature kinds this design declares, prefix and suffix stripped.

        A **set**, not a count: the stream declares each kind once, so there is
        no number here to report.  How many extrudes a design actually has is
        the timeline's business, not the registry's.
        """
        return {
            name[len(FEATURE_PREFIX) : -len(FEATURE_SUFFIX)]
            for _, name in self.named_types()
            if is_feature_type(name)
        }


def is_feature_type(name: str) -> bool:
    """True for a timeline feature's meta-type, e.g. ``DcExtrudeFeatureMetaType``."""
    return name.startswith(FEATURE_PREFIX) and name.endswith(FEATURE_SUFFIX)


@dataclass(slots=True)
class Segment:
    """A segment's two streams, parsed as far as Phase 1 goes."""

    name: str
    segment_type: str
    meta: MetaStream
    bulk: BulkStream
    #: Bytes on disk, before decompression, for size reporting.
    meta_size: int = 0
    bulk_size: int = 0

    @property
    def is_design(self) -> bool:
        """True for a design segment.

        The type is ``FusionDesignSegmentType`` in current documents,
        ``AnimationDesignSegmentType`` for an animation asset, and plain
        ``Design`` in documents written before the type names were qualified.
        """
        return "Design" in (self.segment_type or self.meta.declared_type)

    def body_refs(self) -> list[str]:
        """B-Rep blob filenames this segment references, in first-seen order.

        The design graph names its bodies by blob file, which is how a
        ``BREP.<uuid>.smb`` in ``Breps.BlobParts`` is tied back to the
        component that owns it.
        """
        seen: dict[str, None] = {}
        for match in BREP_REF_WIDE_RE.findall(self.bulk.body):
            seen.setdefault(match.decode("utf-16-le"), None)
        for match in BREP_REF_RE.findall(self.bulk.body):
            seen.setdefault(match.decode("ascii"), None)
        return list(seen)

    def strings(self, min_len: int = 4) -> list[str]:
        """Distinct printable strings in the bulk payload, in first-seen order."""
        seen: dict[str, None] = {}
        for found in scan_strings(self.bulk.body, min_len=min_len):
            seen.setdefault(found.value, None)
        return list(seen)


def _readable(text: str) -> bool:
    return bool(text) and text.isascii() and text.isidentifier()


def _read_version_block(r: Reader) -> tuple[tuple[int, ...], str, str]:
    """Read the version words plus the segment type and owner that follow.

    Width varies by document revision, so each candidate is accepted only when
    two readable identifiers decode immediately after it.
    """
    anchor = r.pos
    for words in _VERSION_BLOCK_WORDS:
        r.seek(anchor)
        try:
            reserved = tuple(r.u32() for _ in range(words))
            declared_type = r.str8()
            owner = r.str8()
        except StreamError:
            continue
        # Older revisions leave the owner blank, so only the type must read.
        if _readable(declared_type) and (not owner or _readable(owner)):
            return reserved, declared_type, owner
    raise StreamError(f"no readable segment type after the meta stream header at {anchor}")


def read_meta_stream(data: bytes) -> MetaStream:
    """Parse a ``MetaStream.dat`` header."""
    r = Reader(data)
    prefix = r.str8()
    slot = r.u32()
    guid = r.wstr()
    reserved, declared_type, owner = _read_version_block(r)
    return MetaStream(
        prefix=prefix,
        slot=slot,
        guid=guid,
        reserved=reserved,
        declared_type=declared_type,
        owner=owner,
        body_offset=r.pos,
        body=data[r.pos :],
    )


def read_bulk_stream(data: bytes) -> BulkStream:
    """Parse a ``BulkStream.dat`` header."""
    r = Reader(data)
    version = r.str8()
    flags = r.u64()
    return BulkStream(version=version, flags=flags, body_offset=r.pos, body=data[r.pos :])


def read_segment(
    name: str,
    meta_bytes: bytes,
    bulk_bytes: bytes,
    *,
    segment_type: str = "",
) -> Segment:
    """Read both streams of one segment.

    *segment_type* comes from the asset manifest's declaration; when it is not
    supplied the meta stream's own ``declared_type`` is used instead.
    """
    meta = read_meta_stream(meta_bytes)
    return Segment(
        name=name,
        segment_type=segment_type or meta.declared_type,
        meta=meta,
        bulk=read_bulk_stream(bulk_bytes),
    )
