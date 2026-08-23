"""Turn a :class:`~ezf3d.model.document.Document` into report models.

Kept out of the CLI so the same summaries are available from library code, and
so the JSON contract has exactly one implementation.
"""

from __future__ import annotations

from collections import Counter

from ezf3d.asm.brep import Shape
from ezf3d.asm.geometry import SplineSurface
from ezf3d.asm.header import AsmError, read_header
from ezf3d.asm.topology import KERNEL_UNIT
from ezf3d.model.document import Asset, Body, Document
from ezf3d.model.report import (
    AssetInfo,
    BodyInfo,
    BoundsInfo,
    DocumentInfo,
    KernelInfo,
    PackageInfo,
    PackageMember,
    SegmentInfo,
    Totals,
)

#: Enough bytes to cover the ASM header of any body.
_HEADER_PREFIX = 256


def segment_info(document: Document, asset: Asset) -> list[SegmentInfo]:
    rows: list[SegmentInfo] = []
    for name, segment in sorted(asset.segments.items()):
        rows.append(
            SegmentInfo(
                name=name,
                type=segment.segment_type or segment.meta.declared_type,
                version=segment.bulk.version,
                meta_size=segment.meta_size,
                bulk_size=segment.bulk_size,
                is_design=segment.is_design,
                declared_features=sorted(segment.bulk.declared_feature_types()),
                feature_registries=len(segment.bulk.feature_registries()),
            )
        )
    return rows


def asset_info(document: Document, asset: Asset) -> AssetInfo:
    return AssetInfo(
        folder=asset.folder,
        name=asset.name,
        state=asset.state,
        asset_type=asset.manifest.asset_type,
        schema_versions=dict(sorted(asset.manifest.schema.items())),
        segments=segment_info(document, asset),
        bodies=len(asset.bodies),
        materials=len(asset.layout.proteins),
        previews=len(asset.layout.previews),
        images=len(asset.layout.images),
        has_graphics_cache=asset.has_graphics_cache,
    )


def kernel_info(document: Document) -> KernelInfo:
    """Read only each body's ASM header — cheap regardless of body size."""
    versions: set[str] = set()
    widths: set[int] = set()
    products: set[str] = set()
    for body in document.bodies:
        try:
            header = read_header(document.archive.read_prefix(body.path, _HEADER_PREFIX))
        except (AsmError, IndexError):
            continue
        versions.add(header.kernel_release)
        widths.add(header.word_size)
        products.add(header.product)
    return KernelInfo(
        versions=sorted(versions), word_sizes=sorted(widths), products=sorted(products)
    )


def package_info(document: Document) -> PackageInfo | None:
    if document.package is None:
        return None
    return PackageInfo(
        root=document.package.root_path,
        members=[
            PackageMember(
                id=entry.object_id,
                name=entry.name,
                path=entry.path,
                version=entry.version,
                xrefs=list(entry.xrefs),
                description=entry.description,
            )
            for entry in document.package.entries.values()
        ],
    )


def document_info(document: Document, *, recurse: bool = True) -> DocumentInfo:
    """Summarise a document without parsing any B-Rep body."""
    manifest = document.manifest
    entries = list(document.archive.entries())
    compression: Counter[str] = Counter(e.method_name for e in entries)
    body_bytes = sum(b.size for b in document.bodies)

    info = DocumentInfo(
        name=document.name,
        doc_type=manifest.doc_type,
        extension=manifest.extension,
        format_version=manifest.format_version,
        document_guid=manifest.document_guid,
        lineage_guid=manifest.lineage_guid,
        origin=manifest.origin,
        schema_versions=dict(sorted(manifest.schema.items())),
        compression=dict(sorted(compression.items())),
        kernel=kernel_info(document),
        assets=[asset_info(document, a) for a in document.assets.values()],
        totals=Totals(
            entries=len(entries),
            compressed_bytes=sum(e.compressed_size for e in entries),
            uncompressed_bytes=sum(e.size for e in entries),
            bodies=len(document.bodies),
            body_bytes=body_bytes,
        ),
        package=package_info(document),
    )
    if recurse:
        info.linked = [document_info(child, recurse=True) for child in document.linked.values()]
    return info


def body_info(document: Document, asset: Asset, body: Body) -> BodyInfo:
    """Parse one body and summarise its topology and geometry."""
    model = body.model()
    stats = body.census()
    design = asset.design
    referenced = bool(design) and f"BREP.{body.uuid}.{body.suffix}" in set(design.body_refs())

    bounds = None
    if stats.vertex_bounds is not None:
        bounds = BoundsInfo(
            min=stats.vertex_bounds.min,
            max=stats.vertex_bounds.max,
            size=stats.vertex_bounds.size,
            unit=KERNEL_UNIT,
        )

    shape = Shape(model)
    live_faces = list(shape.faces())
    analytic = sum(
        1
        for face in live_faces
        if face.surface_entity is not None and not isinstance(face.surface, SplineSurface)
    )

    return BodyInfo(
        uuid=body.uuid,
        path=body.path,
        document=document.name,
        asset=asset.folder,
        suffix=body.suffix,
        size=body.size,
        has_history=model.has_history,
        kernel=model.header.kernel_release,
        word_size=model.header.word_size,
        entities=stats.entities,
        topology=dict(sorted(stats.topology.items())),
        surfaces=dict(sorted(stats.surfaces.items())),
        curves=dict(sorted(stats.curves.items())),
        attributes=dict(sorted(stats.attributes.items())),
        spline_fraction=round(stats.spline_fraction, 4),
        analytic_only=stats.analytic_only,
        vertex_bounds=bounds,
        referenced_by_design=referenced,
        solids=sum(1 for _ in shape.solids()),
        live_faces=len(live_faces),
        live_edges=sum(1 for _ in shape.edges()),
        analytic_faces=analytic,
    )


def body_infos(document: Document, *, recurse: bool = True) -> list[BodyInfo]:
    """Summarise every body in *document*, and in linked documents."""
    rows: list[BodyInfo] = []
    docs = document.documents() if recurse else [document]
    for doc in docs:
        for asset in doc.assets.values():
            for body in asset.bodies:
                rows.append(body_info(doc, asset, body))
    return rows
