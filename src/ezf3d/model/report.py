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
    #: Occurrences of each timeline meta-type name in the payload.  An
    #: indicator of which feature kinds the design uses, not an exact count.
    feature_types: dict[str, int] = Field(default_factory=dict)


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


DocumentInfo.model_rebuild()
