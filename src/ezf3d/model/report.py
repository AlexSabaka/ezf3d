"""JSON output contract for the CLI.

Every command emits the same envelope, so an agent can branch on ``ok`` before
looking at anything else::

    {"ok": true, "command": "info", "source": "...", "data": {...}}
    {"ok": false, "command": "info", "source": "...", "error": "..."}

``info`` and ``bodies`` — the two an agent parses most — have declared payload
schemas; the forensic commands carry free-form payloads.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Envelope(BaseModel):
    """Wrapper shared by every command."""

    ok: bool = True
    command: str
    source: str
    data: Any = None
    error: str | None = None


class SegmentInfo(BaseModel):
    name: str
    type: str
    #: Bulk-stream schema revision, e.g. ``397``.
    version: str
    meta_size: int
    bulk_size: int
    is_design: bool = False
    #: Timeline feature kinds this segment's registries declare, sorted.  A
    #: list rather than a count on purpose: the stream declares each kind once
    #: per registry, so a number here would describe the dictionary and not
    #: the design.
    declared_features: list[str] = Field(default_factory=list)
    #: How many registries the segment holds — one per component that can own
    #: a timeline.
    feature_registries: int = 0
    #: Module states in the meta stream's chain.
    meta_records: int = 0
    #: Objects the meta stream indexes into the bulk stream, each with a known
    #: offset and extent.
    objects: int = 0
    #: One past the highest object id ever issued in this segment.
    next_object_id: int = 0
    #: Schema revision per subsystem, from the meta stream's footer.
    meta_schema: dict[str, int] = Field(default_factory=dict)
    #: Meta-stream bytes this reader did not account for.
    meta_unread: int = 0


class ComponentInfo(BaseModel):
    """One component of a design."""

    oid: int
    #: Fusion writes a GUID here for a component the user never named.
    name: str
    named: bool = True
    #: Blob filenames of the bodies this component owns.
    bodies: list[str] = Field(default_factory=list)
    #: Feature kinds its own registry declares — what it *can* contain.
    declared_features: list[str] = Field(default_factory=list)


class DesignInfo(BaseModel):
    """A design segment's structure."""

    document: str
    objects: int = 0
    roots: dict[str, int] = Field(default_factory=dict)
    components: list[ComponentInfo] = Field(default_factory=list)
    #: Bodies the graph names, against those in ``Breps.BlobParts``.
    bodies_named: int = 0
    bodies_on_disk: int = 0


class AssetInfo(BaseModel):
    folder: str
    name: str
    state: str | None = None
    asset_type: str = ""
    schema_versions: dict[str, int] = Field(default_factory=dict)
    segments: list[SegmentInfo] = Field(default_factory=list)
    bodies: int = 0
    materials: int = 0
    previews: int = 0
    images: int = 0
    has_graphics_cache: bool = False


class PackageMember(BaseModel):
    id: int
    name: str
    path: str
    version: int = 1
    xrefs: list[int] = Field(default_factory=list)
    description: str | None = None


class PackageInfo(BaseModel):
    root: str | None = None
    members: list[PackageMember] = Field(default_factory=list)


class KernelInfo(BaseModel):
    """ASM kernel versions found across a document's bodies."""

    versions: list[str] = Field(default_factory=list)
    word_sizes: list[int] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)


class Totals(BaseModel):
    entries: int = 0
    compressed_bytes: int = 0
    uncompressed_bytes: int = 0
    bodies: int = 0
    body_bytes: int = 0


class DocumentInfo(BaseModel):
    """Payload of ``ezf3d info``."""

    name: str
    doc_type: str
    extension: str
    format_version: str
    document_guid: str
    lineage_guid: str
    origin: str
    schema_versions: dict[str, int] = Field(default_factory=dict)
    compression: dict[str, int] = Field(default_factory=dict)
    kernel: KernelInfo = Field(default_factory=KernelInfo)
    assets: list[AssetInfo] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    package: PackageInfo | None = None
    linked: list[DocumentInfo] = Field(default_factory=list)


class BoundsInfo(BaseModel):
    min: tuple[float, float, float]
    max: tuple[float, float, float]
    size: tuple[float, float, float]
    unit: str = "cm"


class BodyInfo(BaseModel):
    """Payload row of ``ezf3d bodies``."""

    uuid: str
    path: str
    document: str
    asset: str
    suffix: str
    size: int
    has_history: bool
    kernel: str = ""
    word_size: int = 8
    entities: int = 0
    topology: dict[str, int] = Field(default_factory=dict)
    surfaces: dict[str, int] = Field(default_factory=dict)
    curves: dict[str, int] = Field(default_factory=dict)
    attributes: dict[str, int] = Field(default_factory=dict)
    #: Fraction of surfaces needing spline evaluation rather than an analytic form.
    spline_fraction: float = 0.0
    analytic_only: bool = False
    #: Bounds of the B-Rep vertices; faces may bulge outside them.
    vertex_bounds: BoundsInfo | None = None
    referenced_by_design: bool = False
    #: Distinct solids, after collapsing the duplicate ``body`` records a
    #: rolled-back design leaves behind.
    solids: int = 0
    #: Faces and edges reachable from a body.  Lower than the record counts
    #: above when rollback history has left stale topology in the file.
    live_faces: int = 0
    live_edges: int = 0
    #: Reachable faces whose surface ezf3d can evaluate today: everything
    #: except splines, which land in Phase 2.4.
    analytic_faces: int = 0


DocumentInfo.model_rebuild()
