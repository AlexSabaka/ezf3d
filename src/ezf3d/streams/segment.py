"""Readers for a segment's ``MetaStream.dat`` / ``BulkStream.dat`` pair.

A *segment* is one subsystem's slice of the document — the design timeline, the
browser tree, the assembly-context tree.  Each is stored as two files:

``MetaStream.dat``
    The index, and it earns the name: after the header it holds one record per
    saved module state, then a table mapping **object id to byte offset in the
    bulk stream**.  Because the offsets ascend with the ids, consecutive
    entries delimit each record — so the bulk stream is randomly addressable
    and its objects have known extents.

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

Headers, the meta record chain and the object index are decoded here.  The
*contents* of a bulk object still need a schema-versioned decoder; until then
:meth:`Segment.object_bytes` hands back exactly the bytes of one.
"""

from __future__ import annotations

import re
import struct
from collections import Counter
from dataclasses import dataclass, field

from ezf3d.streams.primitives import Reader, StreamError, scan_strings

#: The two-entry table every meta stream ends with, naming the schema revision
#: of the subsystems that wrote it.
FOOTER_NAMES = ("Application", "Server")

#: Longest ``str8`` treated as a name while walking meta records.  Guids run to
#: 37 characters -- Fusion writes at least one with five dashes rather than
#: four -- and module names are shorter still.
_MAX_META_NAME = 256

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


@dataclass(frozen=True, slots=True)
class MetaRecord:
    """One entry of a segment's meta record list.

    Two GUIDs, and what separates them is how they repeat.  :attr:`identity`
    is unique across the list in all fourteen segments of the samples -- 299
    distinct across Robotic_Bhujha's 299 records.  :attr:`group` repeats
    heavily, 45 distinct values over the wheel's 167 records with one of them
    used 22 times, so it names something several records share.

    The first few records of a segment look like a chain, each ``group``
    turning up as the next record's ``identity``.  That does not survive the
    whole list -- 44 of the wheel's 167 -- so it is a coincidence of the
    opening records rather than the structure, and no chain is claimed here.
    """

    #: This record's own GUID; unique within the segment.
    identity: str
    #: A GUID several records share.  What it names is not established.
    group: str
    #: Small enum, 0-4.  Meaning not established.
    kind: int
    #: Subsystem that wrote it -- ``Fusion``, ``Geometry``, ``EntityTracking``,
    #: ``Component``, ``Scene``, ``Body``, ``MSketch``, ``CommonData``.
    owner: str
    #: Object ids this record refers to.
    ids: tuple[int, ...] = ()


@dataclass(slots=True)
class MetaStream:
    """Parsed ``MetaStream.dat``: the header, the record chain and the index."""

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
    #: Record count the header declares, which the list always matches.
    declared_records: int = 0
    #: The records, in file order.
    records: list[MetaRecord] = field(default_factory=list)
    #: Object ids the segment names as its roots.
    roots: tuple[int, ...] = ()
    #: **Object id to byte offset in the bulk stream.**  Ascending in both, so
    #: consecutive entries give each object's extent.
    index: dict[int, int] = field(default_factory=dict)
    #: One past the highest object id ever issued -- 453 in a segment whose
    #: largest indexed id is 452.
    next_id: int = 0
    #: Schema revision per subsystem, from the footer.
    schema: dict[str, int] = field(default_factory=dict)
    #: Bytes between the index and the footer that this reader did not
    #: account for.  Zero in the plain documents; the ``.f3z`` members carry a
    #: further section holding a wide GUID, which is counted rather than
    #: guessed at.
    unread: int = 0


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


@dataclass(frozen=True, slots=True)
class BulkObject:
    """One object of the bulk stream, located by the meta stream's index."""

    #: Object id, as the meta stream's records refer to it.
    oid: int
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


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

    def objects(self) -> list[BulkObject]:
        """Every indexed object, in bulk-stream order, with its extent.

        The index gives each object's start; the next object's start gives its
        end, and the last runs to the end of the payload.  Offsets ascend with
        ids and never repeat, in every sample.
        """
        ordered = sorted(self.meta.index.items(), key=lambda item: item[1])
        limit = len(self.bulk.body)
        return [
            BulkObject(
                oid=oid,
                offset=offset,
                size=(ordered[i + 1][1] if i + 1 < len(ordered) else limit) - offset,
            )
            for i, (oid, offset) in enumerate(ordered)
        ]

    def object_bytes(self, oid: int) -> bytes:
        """Exactly the bytes of one object, or ``b""`` if it is not indexed."""
        for item in self.objects():
            if item.oid == oid:
                return self.bulk.body[item.offset : item.end]
        return b""

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


def _name_at(body: bytes, pos: int) -> tuple[str, int] | None:
    """A ``str8`` at *pos*, or ``None`` if one does not start there.

    Empty is a legitimate value -- some records name no group and some no
    owner -- so zero length reads as the empty string rather than a failure.
    """
    if pos < 0 or pos + 4 > len(body):
        return None
    size = int.from_bytes(body[pos : pos + 4], "little")
    if size > _MAX_META_NAME or pos + 4 + size > len(body):
        return None
    raw = body[pos + 4 : pos + 4 + size]
    if any(char < 0x20 or char >= 0x7F for char in raw):
        return None
    return raw.decode("ascii"), pos + 4 + size


def _read_footer(body: bytes) -> tuple[dict[str, int], int]:
    """``({subsystem: revision}, offset it starts at)`` from the end of *body*.

    Located by searching backwards for its first name rather than by counting
    forwards, so an unread section before it cannot hide it.
    """
    for pos in range(len(body) - 8, 0, -1):
        found = _name_at(body, pos)
        if found is None or found[0] != FOOTER_NAMES[0]:
            continue
        start = pos - 4
        schema: dict[str, int] = {}
        cursor = pos
        for _ in FOOTER_NAMES:
            entry = _name_at(body, cursor)
            if entry is None or cursor + 4 > len(body):
                return {}, len(body)
            name, cursor = entry
            if cursor + 4 > len(body):
                return {}, len(body)
            schema[name] = int.from_bytes(body[cursor : cursor + 4], "little")
            cursor += 4
        return schema, start
    return {}, len(body)


def _read_meta_body(stream: MetaStream) -> None:
    """Fill in the record chain, the roots, the object index and the footer.

    Laid out as::

        u32 ?, u32 ?, u32 record_count
        record_count x { str8 identity, str8 group, u32 kind,
                         str8 owner, u32 n, n x u64 ids }
        u32 n_roots, n_roots x u64
        { u32 n, n x (u64 object_id, u64 bulk_offset) } ...
        u64 next_id, u32 0
        u32 2, { str8 subsystem, u32 revision } x 2
    """
    body = stream.body
    if len(body) < 12:
        return
    stream.declared_records = int.from_bytes(body[8:12], "little")
    stream.schema, footer = _read_footer(body)

    pos = 12
    for _ in range(stream.declared_records):
        identity = _name_at(body, pos)
        if identity is None:
            break
        group = _name_at(body, identity[1])
        if group is None or group[1] + 4 > len(body):
            break
        kind = int.from_bytes(body[group[1] : group[1] + 4], "little")
        owner = _name_at(body, group[1] + 4)
        if owner is None or owner[1] + 4 > len(body):
            break
        count = int.from_bytes(body[owner[1] : owner[1] + 4], "little")
        start = owner[1] + 4
        if count > len(body) // 8 or start + 8 * count > len(body):
            break
        ids = tuple(
            int.from_bytes(body[start + 8 * i : start + 8 * i + 8], "little") for i in range(count)
        )
        stream.records.append(
            MetaRecord(identity=identity[0], group=group[0], kind=kind, owner=owner[0], ids=ids)
        )
        pos = start + 8 * count

    if pos + 4 <= footer:
        roots = int.from_bytes(body[pos : pos + 4], "little")
        end = pos + 4 + 8 * roots
        if end <= footer:
            stream.roots = struct.unpack_from(f"<{roots}Q", body, pos + 4) if roots else ()
            pos = end

    # The index arrives as one or more tables; later ones revise earlier ones,
    # so they are merged in the order written.
    while pos + 4 <= footer:
        count = int.from_bytes(body[pos : pos + 4], "little")
        end = pos + 4 + 16 * count
        if end > footer:
            break
        flat = struct.unpack_from(f"<{2 * count}Q", body, pos + 4) if count else ()
        stream.index.update(zip(flat[0::2], flat[1::2], strict=True))
        pos = end
        if count == 0:
            break

    if pos + 12 <= footer:
        stream.next_id = int.from_bytes(body[pos : pos + 8], "little")
        pos += 12
    stream.unread = max(0, footer - pos)


def read_meta_stream(data: bytes) -> MetaStream:
    """Parse a ``MetaStream.dat``."""
    r = Reader(data)
    prefix = r.str8()
    slot = r.u32()
    guid = r.wstr()
    reserved, declared_type, owner = _read_version_block(r)
    stream = MetaStream(
        prefix=prefix,
        slot=slot,
        guid=guid,
        reserved=reserved,
        declared_type=declared_type,
        owner=owner,
        body_offset=r.pos,
        body=data[r.pos :],
    )
    _read_meta_body(stream)
    return stream


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
