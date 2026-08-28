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
from ezf3d.model.design import read_design
from ezf3d.model.document import Asset, Body, Document
from ezf3d.model.materials import Materials, read_assignments
from ezf3d.model.parameters import read_parameters
from ezf3d.model.report import (
    AssetInfo,
    BodyInfo,
    BoundsInfo,
    ComponentInfo,
    DesignInfo,
    DocumentInfo,
    KernelInfo,
    PackageInfo,
    PackageMember,
    ParameterInfo,
    ParametersInfo,
    SegmentInfo,
    SketchesInfo,
    SketchInfo,
    TimelineEntry,
    TimelineInfo,
    Totals,
)
from ezf3d.model.sketch import read_sketches
from ezf3d.model.timeline import read_timeline

#: Enough bytes to cover the ASM header of any body.
_HEADER_PREFIX = 256


def design_infos(document: Document) -> list[DesignInfo]:
    """One :class:`DesignInfo` per document that carries a design segment."""
    rows: list[DesignInfo] = []
    for child in document.documents():
        segment = child.design
        if segment is None:
            continue
        design = read_design(segment)
        on_disk = {f"BREP.{body.uuid}.{body.suffix}" for body in child.bodies}
        materials = read_materials(child)
        assigned = materials.by_object()
        owned: Counter[int] = Counter()
        for assignment in materials:
            owner = design.owner(assignment.oid)
            if owner is not None and owner.oid != assignment.oid:
                owned[owner.oid] += 1
        rows.append(
            DesignInfo(
                document=child.name,
                objects=design.objects,
                roots=dict(sorted(design.roots.items())),
                components=[
                    ComponentInfo(
                        oid=component.oid,
                        name=component.name,
                        named=component.is_named,
                        bodies=list(component.bodies),
                        declared_features=sorted(component.features),
                        material=_material_name(assigned.get(component.oid)),
                        appearance=(
                            assigned[component.oid].appearance if component.oid in assigned else ""
                        ),
                        body_materials=owned.get(component.oid, 0),
                    )
                    for component in design.components
                ],
                bodies_named=len(design.bodies),
                bodies_on_disk=len(on_disk),
                assignments=len(materials),
                material_assets=dict(sorted(materials.assets().items())),
                undeclared_assets=list(materials.check()),
            )
        )
    return rows


def _parameter_info(parameter, design) -> ParameterInfo:
    return ParameterInfo(
        oid=parameter.oid,
        name=parameter.name,
        role=parameter.role,
        unit=parameter.unit,
        expression=parameter.expression,
        value=parameter.value,
        display=parameter.display,
        comment=parameter.comment,
        component=_component_name(design, parameter.oid),
        revision=parameter.revision,
    )


def parameter_infos(document: Document) -> list[ParametersInfo]:
    """One :class:`ParametersInfo` per document that carries a design segment.

    Each parameter is attributed to a component by the id-range rule
    :class:`~ezf3d.model.design.Design` documents — the same rule that puts
    bodies under their owners, so the attribution is only as good as that, and
    that one is checked against the archive.
    """
    rows: list[ParametersInfo] = []
    for child in document.documents():
        segment = child.design
        if segment is None:
            continue
        parameters = read_parameters(segment)
        design = read_design(segment) if parameters.values else None
        checked, disagreeing = parameters.literal_check()
        rows.append(
            ParametersInfo(
                document=child.name,
                declared=parameters.declared,
                parameters=[_parameter_info(parameter, design) for parameter in parameters],
                unreadable=list(parameters.unreadable),
                literals_checked=checked,
                literals_disagreeing=list(disagreeing),
                table=parameters.table,
                manager=parameters.manager,
            )
        )
    return rows


def read_materials(child: Document) -> Materials:
    """A document's material assignments, with the catalogue its packages declare."""
    catalogue: dict[str, str] = {}
    for asset in child.assets.values():
        catalogue.update(asset.protein_catalogue())
    segment = child.design
    return Materials(
        assignments=read_assignments(segment) if segment is not None else [],
        catalogue=catalogue,
    )


def _material_name(assignment) -> str:
    """What to show for a material: the design's own name, else the asset id."""
    if assignment is None:
        return ""
    return assignment.material or assignment.asset


def _component_name(design, oid: int) -> str:
    owner = design.owner(oid) if design is not None else None
    return owner.name if owner is not None else ""


def timeline_infos(document: Document) -> list[TimelineInfo]:
    """One :class:`TimelineInfo` per document that carries a design segment.

    Each feature is attributed to a component by the same id-range rule the
    body mapping is checked on; the timeline's own order comes from the list,
    not from the ids.
    """
    rows: list[TimelineInfo] = []
    for child in document.documents():
        segment = child.design
        if segment is None:
            continue
        parameters = read_parameters(segment)
        timeline = read_timeline(segment, parameters)
        design = read_design(segment) if timeline.features else None
        unknown, over = timeline.check()
        rows.append(
            TimelineInfo(
                document=child.name,
                oid=timeline.oid,
                entries=[
                    TimelineEntry(
                        index=feature.index,
                        oid=feature.oid,
                        name=feature.name,
                        kind=feature.kind,
                        component=_component_name(design, feature.oid),
                        inputs=len(feature.inputs),
                        parameters=[_parameter_info(p, design) for p in feature.parameters],
                        operation=feature.extrude.operation if feature.extrude else "",
                        direction=feature.extrude.direction if feature.extrude else "",
                    )
                    for feature in timeline
                ],
                unnamed=timeline.unnamed(),
                declared=dict(sorted(timeline.declared.items())),
                census=dict(sorted(timeline.census().items())),
                outside=dict(sorted(timeline.outside.items())),
                unknown_labels=list(unknown),
                over_counter=[
                    f"{kind}: {live} live, {counter} issued" for kind, live, counter in over
                ],
            )
        )
    return rows


def sketch_infos(document: Document) -> list[SketchesInfo]:
    """One :class:`SketchesInfo` per document that carries a design segment.

    A sketch's geometry is reached from the geometry, not from the timeline:
    each point and curve names the sketch that owns it. The timeline is read
    only for what the sketches are called and where they sit in the run order,
    and a sketch the list does not hold still appears, with index ``-1``.
    """
    rows: list[SketchesInfo] = []
    for child in document.documents():
        segment = child.design
        if segment is None:
            continue
        parameters = read_parameters(segment)
        timeline = read_timeline(segment, parameters)
        sketches = read_sketches(segment, timeline)
        design = read_design(segment) if len(sketches) else None
        entries = []
        for sketch in sketches:
            checked, missing = sketch.dimension_check()
            good, bad = sketch.curve_check()
            loops = sketch.loops()
            entries.append(
                SketchInfo(
                    oid=sketch.oid,
                    index=sketch.index,
                    name=sketch.name,
                    component=_component_name(design, sketch.oid),
                    points=len(sketch.points),
                    curves=len(sketch.curves),
                    extent=list(sketch.extent()),
                    coordinates=[(p.x, p.y) for p in sketch.points],
                    parameters=[_parameter_info(p, design) for p in sketch.parameters],
                    dimensions_checked=checked,
                    dimensions_missing=list(missing),
                    kinds=dict(sorted(sketch.kinds().items())),
                    loops=[list(loop.curves) for loop in loops],
                    loose=sketch.loose(),
                    geometry_checked=good,
                    geometry_disagreeing=list(bad),
                )
            )
        rows.append(
            SketchesInfo(
                document=child.name,
                sketches=entries,
                points=sketches.points(),
                curves=sketches.curves(),
                kinds=dict(sorted(sketches.kinds().items())),
                loops=sum(len(row.loops) for row in entries),
                loose=sum(row.loose for row in entries),
                unowned=sketches.unowned,
            )
        )
    return rows


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
                meta_records=len(segment.meta.records),
                objects=len(segment.meta.index),
                next_object_id=segment.meta.next_id,
                meta_schema=dict(sorted(segment.meta.schema.items())),
                meta_unread=segment.meta.unread,
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
