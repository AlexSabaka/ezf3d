"""Readers for the two ``Manifest.dat`` flavours.

A Fusion document carries a manifest at two levels:

**Document manifest** (``/Manifest.dat``) — document type, identity GUIDs, the
``{module: schema_version}`` table that tells a reader which schema revision
every subsystem was written with, and the list of asset folders.

**Asset manifest** (``<Asset>[State]/Manifest.dat``) — the asset's identity and
its *segment table*: ``(slot, folder_prefix, segment_type)`` triples.  The
folder on disk is ``prefix`` plus an instance number, which is why
``FusionDesignSegmentType`` in one document and ``Design`` in another both
describe the design segment.  Never key off the folder name; key off the type.

Fields whose meaning is not yet established are preserved verbatim in
``reserved`` rather than dropped, so later phases can mine them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ezf3d.streams.primitives import Reader, StreamError, scan_strings

#: Sentinel that opens the version block of a recent document manifest.  Older
#: documents (and ``.f3z`` members written by the cloud translator) open with a
#: build stamp instead, and carry two fewer words, so the block is located by
#: validating what follows rather than by trusting a fixed layout.
MANIFEST_COOKIE = 1234

#: Candidate sizes, in words, of the version block between the identity GUIDs
#: and the schema table.
_VERSION_BLOCK_WORDS = (4, 2, 6)

#: Sanity bounds used to reject a misparsed count.
_MAX_SEGMENTS = 64
_MAX_SCHEMA_ENTRIES = 64


@dataclass(slots=True)
class ManifestItem:
    """An entry of the document manifest's optional item list."""

    fields: tuple[str, str, str, str]
    numbers: tuple[int, int, int, int]
    properties: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentManifest:
    """Parsed ``/Manifest.dat``."""

    format_version: str
    doc_type_key: str
    extension: str
    doc_type: str
    doc_description: str
    document_guid: str
    lineage_guid: str
    schema: dict[str, int]
    related_guids: list[str]
    content_guid: str
    asset_names: list[str]
    #: Provenance marker.  ``NA_OFFLINESAVE`` / ``NA_EXPORT`` for a document
    #: saved from the desktop app; the design's name for one produced by the
    #: cloud translator.
    origin: str
    items: list[ManifestItem] = field(default_factory=list)
    reserved: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class SegmentDeclaration:
    """One row of an asset manifest's segment table."""

    slot: int
    prefix: str
    segment_type: str

    def matches(self, folder: str) -> bool:
        """True if *folder* is an instance of this declaration (``prefix`` + N)."""
        return folder == self.prefix or (
            folder.startswith(self.prefix) and folder[len(self.prefix) :].isdigit()
        )


@dataclass(slots=True)
class AssetManifest:
    """Parsed ``<Asset>[State]/Manifest.dat``."""

    asset_name: str
    asset_guid: str
    revision_guid: str
    asset_type_key: str
    schema: dict[str, int]
    asset_type: str
    properties: dict[str, str] = field(default_factory=dict)
    #: GUIDs of assets this one derives from (``Animation`` -> its design).
    parent_guids: list[str] = field(default_factory=list)
    segments: list[SegmentDeclaration] = field(default_factory=list)
    change_counter: int = 0
    #: Bytes left unconsumed — non-empty means an undecoded schema revision.
    trailing: bytes = b""


def _read_version_block(r: Reader) -> tuple[tuple[int, ...], dict[str, int]]:
    """Read the version words plus the schema table that follows them.

    The block's width varies by document revision, so each candidate width is
    tried and accepted only if a plausible schema table decodes right after it.
    """
    anchor = r.pos
    for words in _VERSION_BLOCK_WORDS:
        r.seek(anchor)
        try:
            reserved = tuple(r.u32() for _ in range(words))
            table = r.str8_u32_table()
        except StreamError:
            continue
        if _plausible_schema(table):
            return reserved, table
    raise StreamError(f"no readable schema table after the identity GUIDs at {anchor}")


def _plausible_schema(table: dict[str, int]) -> bool:
    if not 0 < len(table) <= _MAX_SCHEMA_ENTRIES:
        return False
    return all(
        name.isascii() and name.isidentifier() and 0 <= version < 1 << 24
        for name, version in table.items()
    )


def read_document_manifest(data: bytes) -> DocumentManifest:
    """Parse the top-level ``Manifest.dat``."""
    r = Reader(data)
    format_version = r.str8()
    doc_type_key = r.str8()
    extension = r.wstr()
    doc_type = r.wstr()
    doc_description = r.wstr()
    document_guid = r.wstr()
    lineage_guid = r.wstr()

    reserved, schema = _read_version_block(r)
    related_guids = [r.wstr() for _ in range(r.u32())]

    items: list[ManifestItem] = []
    if r.u8():
        for _ in range(r.u32()):
            texts = (r.wstr(), r.wstr(), r.wstr(), r.wstr())
            numbers = (r.u32(), r.u32(), r.u32(), r.u32())
            props = {r.wstr(): r.wstr() for _ in range(r.u32())}
            items.append(ManifestItem(texts, numbers, props))

    content_guid = r.wstr()
    asset_names = [r.wstr() for _ in range(r.u32())]
    r.u32()  # always 0 in observed files
    r.u8()  # always 1 in observed files
    origin = r.wstr()

    return DocumentManifest(
        format_version=format_version,
        doc_type_key=doc_type_key,
        extension=extension,
        doc_type=doc_type,
        doc_description=doc_description,
        document_guid=document_guid,
        lineage_guid=lineage_guid,
        schema=schema,
        related_guids=related_guids,
        content_guid=content_guid,
        asset_names=asset_names,
        origin=origin,
        items=items,
        reserved=reserved,
    )


def read_asset_manifest(data: bytes) -> AssetManifest:
    """Parse an asset folder's ``Manifest.dat``."""
    r = Reader(data)
    manifest = AssetManifest(
        asset_name=r.wstr(),
        asset_guid=r.wstr(),
        revision_guid=r.wstr(),
        asset_type_key=r.str8(),
        schema={},
        asset_type="",
    )
    r.u32()  # schema-table tag, 20 in every observed file
    manifest.schema = r.str8_u32_table()
    manifest.asset_type = r.str8()
    r.u32()
    r.u8()
    manifest.properties = {r.str8(): r.wstr() for _ in range(r.u32())}

    anchor = r.pos
    try:
        _read_asset_tail(r, manifest)
        if r.remaining:
            raise StreamError(f"{r.remaining} bytes left after the segment table")
    except StreamError:
        # Unrecognised revision of the reference block.  The segment table is
        # self-describing enough to find on its own, so recover rather than
        # give up: everything before it is metadata we can afford to skip.
        manifest.parent_guids.clear()
        manifest.segments.clear()
        r.seek(anchor)
        if not _recover_segment_table(data, r, manifest):
            manifest.trailing = data[anchor:]
            return manifest
    manifest.trailing = data[r.pos :]
    return manifest


def _read_asset_tail(r: Reader, manifest: AssetManifest) -> None:
    """Reference block, change counter and segment table, strict reading."""
    for _ in range(r.u32()):
        r.u32()  # reference kind; 0 in every observed file
        manifest.parent_guids.append(r.guid())
        manifest.parent_guids.append(r.guid())
        r.u32()
        r.u32()
        manifest.parent_guids.append(r.guid())
    manifest.change_counter = r.u32()
    count = r.u32()
    if count > _MAX_SEGMENTS:
        raise StreamError(f"implausible segment count {count}")
    for _ in range(count):
        manifest.segments.append(SegmentDeclaration(r.u32(), r.str8(), r.str8()))


def _recover_segment_table(data: bytes, r: Reader, manifest: AssetManifest) -> bool:
    """Locate the segment table by shape when the preamble is unrecognised.

    The table is a run of ``(uint32, str8, str8)`` triples that ends exactly at
    end-of-buffer and is preceded by its own ``uint32`` count, so a candidate
    offset can be checked rather than guessed.
    """
    for found in scan_strings(data, start=r.pos, min_len=3):
        start = found.offset - 4
        if start < r.pos:
            continue
        probe = Reader(data, start)
        try:
            rows = []
            while not probe.eof:
                rows.append(SegmentDeclaration(probe.u32(), probe.str8(), probe.str8()))
        except StreamError:
            continue
        if not rows or probe.pos != len(data):
            continue
        if not all(row.prefix and row.segment_type for row in rows):
            continue
        count_at = start - 4
        if count_at >= r.pos and Reader(data, count_at).u32() != len(rows):
            continue
        manifest.segments.extend(rows)
        r.seek(len(data))
        return True
    return False
