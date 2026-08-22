"""Readers for a segment's ``MetaStream.dat`` / ``BulkStream.dat`` pair.

A *segment* is one subsystem's slice of the document — the design timeline, the
browser tree, the assembly-context tree.  Each is stored as two files:

``MetaStream.dat``
    The index: segment type, the GUIDs of the schema modules that wrote it, and
    per-module records naming the object ids present in the bulk stream.

``BulkStream.dat``
    The payload: a typed object graph, **uncompressed**, whose records carry
    readable meta-type names (``DcExtrudeFeatureMetaType``, ``SketchesRoot``,
    ...).  Those names are what make the design timeline recoverable.

Phase 1 decodes both headers and censuses the bulk stream's type names.  The
per-record bodies need a schema-versioned decoder and land in Phase 3; until
then :attr:`Segment.body` keeps the undecoded bytes reachable.
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
#: features and sketches; ``*Root`` are the container objects that own them.
TYPE_NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9_]{3,60}(?:MetaType|Root|Manager|Attributes)")

#: Meta-type names that denote a timeline feature rather than a container.
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

    def type_names(self) -> Counter[str]:
        """Census of meta-type / root names present in the payload."""
        return Counter(m.decode("ascii") for m in TYPE_NAME_RE.findall(self.body))

    def feature_types(self) -> Counter[str]:
        """Just the timeline meta-types, ``Dc``-prefix and suffix stripped."""
        counts: Counter[str] = Counter()
        for name, n in self.type_names().items():
            if not name.endswith(FEATURE_SUFFIX):
                continue
            short = name[: -len(FEATURE_SUFFIX)]
            counts[short.removeprefix("Dc")] = n
        return counts


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
