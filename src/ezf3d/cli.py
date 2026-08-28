"""``ezf3d`` command line.

Every command takes ``--json`` and emits the envelope from
:mod:`ezf3d.model.report`; without it, output is a Rich rendering of the same
data for humans.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from ezf3d import __version__
from ezf3d.asm.brep import Shape
from ezf3d.asm.header import AsmError, read_header
from ezf3d.asm.records import parse as parse_asm
from ezf3d.asm.tokens import Tag
from ezf3d.container.archive import UnsupportedCompressionError
from ezf3d.export.writers import CM_TO_MM, FORMATS, ExportError, write_mesh
from ezf3d.inspect import (
    body_infos,
    design_infos,
    document_info,
    parameter_infos,
    sketch_infos,
    timeline_infos,
)
from ezf3d.mesh.polyline import DEFAULT_CHORD_TOLERANCE
from ezf3d.model.document import Document, Ef3dError, readfile
from ezf3d.model.report import Envelope
from ezf3d.ogs.verify import compare
from ezf3d.render.camera import Camera
from ezf3d.render.png import write as png_write
from ezf3d.render.raster import Style, ink_bounds, render_lines, render_mesh
from ezf3d.render.scene import (
    CachedGeometry,
    Scene,
    build_cached,
    build_mesh,
    build_scene,
    chosen_bodies,
    contact_sheet,
    open_cache,
)
from ezf3d.streams.primitives import StreamError, scan_strings

app = typer.Typer(
    name="ezf3d",
    help="Read Autodesk Fusion 360 .f3d / .f3z designs without Fusion.",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

FileArg = Annotated[Path, typer.Argument(help="A .f3d or .f3z file.", exists=True)]
JsonOpt = Annotated[bool, typer.Option("--json", help="Emit the JSON envelope.")]
SourceOpt = Annotated[
    str,
    typer.Option(
        "--source",
        help="Where triangles come from: asm (tessellate), ogs (Fusion's cache), "
        "or auto (the cache when it covers the whole body).",
    ),
]

SOURCES = ("auto", "asm", "ogs")

#: Failures that mean "this file is not what we expected", not a crash.
READ_ERRORS = (
    Ef3dError,
    StreamError,
    AsmError,
    UnsupportedCompressionError,
    ExportError,
    KeyError,
    OSError,
)


def _emit(command: str, source: Path, payload: Any, as_json: bool) -> None:
    if as_json:
        envelope = Envelope(command=command, source=str(source), data=payload)
        console.print_json(envelope.model_dump_json(exclude_none=False))


def _fail(command: str, source: Path, exc: Exception, as_json: bool) -> None:
    if as_json:
        envelope = Envelope(ok=False, command=command, source=str(source), error=str(exc))
        console.print_json(envelope.model_dump_json(exclude_none=False))
    else:
        err_console.print(f"[bold red]error[/]: {exc}")
    raise typer.Exit(1)


def _dump(model: Any) -> Any:
    return model.model_dump() if hasattr(model, "model_dump") else model


def _si(value: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0  # type: ignore[assignment]
    return str(value)


# -- info ------------------------------------------------------------------


@app.command()
def info(path: FileArg, as_json: JsonOpt = False) -> None:
    """Document identity, schema versions, assets and segments."""
    try:
        with readfile(path) as doc:
            report = document_info(doc)
    except READ_ERRORS as exc:
        _fail("info", path, exc, as_json)
        return
    if as_json:
        _emit("info", path, _dump(report), True)
        return
    _render_info(report)


def _render_info(report: Any, depth: int = 0) -> None:
    pad = "  " * depth
    console.print(
        f"{pad}[bold cyan]{report.name}[/]  "
        f"[dim]{report.doc_type} {report.extension} · {report.format_version}[/]"
    )
    console.print(f"{pad}[dim]guid[/] {report.document_guid}  [dim]origin[/] {report.origin}")
    kernel = report.kernel
    if kernel.versions:
        widths = "/".join(str(w) for w in kernel.word_sizes)
        console.print(
            f"{pad}[dim]kernel[/] ASM {', '.join(kernel.versions)} [dim](BinaryFile{widths})[/]"
        )
    totals = report.totals
    console.print(
        f"{pad}[dim]archive[/] {totals.entries} entries · "
        f"{_si(totals.compressed_bytes)} on disk → {_si(totals.uncompressed_bytes)} · "
        f"{totals.bodies} bodies ({_si(totals.body_bytes)}) · "
        f"compression {report.compression}"
    )

    for asset in report.assets:
        table = Table(
            title=f"{asset.folder}  ·  {asset.asset_type}",
            title_justify="left",
            header_style="bold",
            box=None,
            pad_edge=False,
        )
        for column in ("segment", "type", "ver", "meta", "bulk", "objects", "declares"):
            table.add_column(column, overflow="fold")
        for segment in asset.segments:
            # Kinds the registries allow, not a tally of features: see
            # BulkStream.feature_registries.  Named in full by --json.
            kinds = len(segment.declared_features)
            features = f"{kinds} kinds" if kinds else "—"
            table.add_row(
                segment.name,
                segment.type,
                segment.version,
                _si(segment.meta_size),
                _si(segment.bulk_size),
                f"{segment.objects:,}",
                f"[green]{features}[/]" if segment.is_design else features or "[dim]—[/]",
            )
        console.print(table)
        console.print(
            f"{pad}  [dim]bodies[/] {asset.bodies}  [dim]materials[/] {asset.materials}  "
            f"[dim]preview[/] {'yes' if asset.previews else 'no'}  "
            f"[dim]graphics cache[/] {'yes' if asset.has_graphics_cache else 'no'}"
        )

    if report.package:
        console.print(f"{pad}[bold]package[/] root={report.package.root}")
    for child in report.linked:
        console.print()
        _render_info(child, depth + 1)


# -- tree ------------------------------------------------------------------


@app.command()
def tree(path: FileArg, as_json: JsonOpt = False) -> None:
    """Document, asset, segment and body structure."""
    try:
        with readfile(path) as doc:
            payload = _tree_payload(doc)
            if not as_json:
                _render_tree(doc)
    except READ_ERRORS as exc:
        _fail("tree", path, exc, as_json)
        return
    _emit("tree", path, payload, as_json)


def _tree_payload(doc: Document) -> dict[str, Any]:
    return {
        "name": doc.name,
        "source": doc.source,
        "is_package": doc.is_package,
        "package": _dump(document_info(doc, recurse=False).package),
        "assets": [
            {
                "folder": asset.folder,
                "state": asset.state,
                "segments": sorted(asset.segments),
                "bodies": [
                    {"uuid": b.uuid, "suffix": b.suffix, "size": b.size} for b in asset.bodies
                ],
                "materials": asset.layout.proteins,
                "previews": asset.layout.previews,
                "graphics_cache": asset.layout.ogs,
            }
            for asset in doc.assets.values()
        ],
        "linked": [_tree_payload(child) for child in doc.linked.values()],
    }


def _render_tree(doc: Document, parent: Tree | None = None) -> None:
    label = f"[bold cyan]{doc.name}[/] [dim]{doc.manifest.doc_type}[/]"
    node = Tree(label) if parent is None else parent.add(label)
    for asset in doc.assets.values():
        state = f" [{asset.state}]" if asset.state else ""
        branch = node.add(f"[bold]{asset.folder}[/]{state}")
        for name, segment in sorted(asset.segments.items()):
            flag = " [green](design)[/]" if segment.is_design else ""
            branch.add(
                f"[magenta]{name}[/] [dim]{segment.segment_type} v{segment.bulk.version}[/]{flag}"
            )
        if asset.bodies:
            bodies = branch.add(f"[bold]Breps[/] [dim]({len(asset.bodies)})[/]")
            for body in asset.bodies:
                history = " [yellow](history)[/]" if body.has_history else ""
                bodies.add(f"{body.uuid} [dim]{_si(body.size)}[/]{history}")
        extras = []
        if asset.layout.proteins:
            extras.append(f"{len(asset.layout.proteins)} material blob(s)")
        if asset.layout.previews:
            extras.append("preview")
        if asset.layout.ogs:
            extras.append(f"graphics cache ({len(asset.layout.ogs)} files)")
        if extras:
            branch.add("[dim]" + " · ".join(extras) + "[/]")
    for child in doc.linked.values():
        _render_tree(child, node)
    if parent is None:
        console.print(node)


# -- bodies ----------------------------------------------------------------


@app.command()
def bodies(
    path: FileArg,
    as_json: JsonOpt = False,
    body: Annotated[
        str | None, typer.Option("--body", help="Only this body UUID (prefix match).")
    ] = None,
) -> None:
    """Parse every B-Rep body and report its topology and geometry."""
    try:
        with readfile(path) as doc:
            rows = body_infos(doc)
    except READ_ERRORS as exc:
        _fail("bodies", path, exc, as_json)
        return
    if body:
        rows = [r for r in rows if r.uuid.startswith(body)]
        if not rows:
            _fail("bodies", path, KeyError(f"no body matching {body!r}"), as_json)
            return
    if as_json:
        _emit("bodies", path, [_dump(r) for r in rows], True)
        return

    table = Table(header_style="bold", box=None, pad_edge=False)
    columns = ("body", "doc", "kind", "size", "solids", "faces", "edges", "surfaces", "extent cm")
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        surfaces = ", ".join(f"{k} {v}" for k, v in sorted(row.surfaces.items()))
        if row.spline_fraction:
            surfaces += f"  [yellow]({row.spline_fraction:.0%} spline)[/]"
        extent = (
            " × ".join(f"{v:.2f}" for v in row.vertex_bounds.size) if row.vertex_bounds else "—"
        )
        stale_faces = row.topology.get("face", 0) - row.live_faces
        faces = f"{row.live_faces}"
        if stale_faces > 0:
            faces += f" [dim]+{stale_faces} stale[/]"
        if row.analytic_faces < row.live_faces:
            faces += f"\n[yellow]{row.analytic_faces} analytic[/]"
        table.add_row(
            f"{row.uuid[:8]} [dim].{row.suffix}[/]",
            row.document,
            "history" if row.has_history else "plain",
            _si(row.size),
            str(row.solids),
            faces,
            str(row.live_edges),
            surfaces or "[dim]—[/]",
            extent,
        )
    console.print(table)
    analytic = sum(1 for r in rows if r.analytic_only)
    live = sum(r.live_faces for r in rows)
    evaluable = sum(r.analytic_faces for r in rows)
    console.print(
        f"[dim]{len(rows)} bodies · {analytic} fully analytic · "
        f"{evaluable}/{live} faces have an evaluable surface today "
        f"(splines land in Phase 2.4)[/]"
    )


# -- dump ------------------------------------------------------------------


@app.command()
def dump(
    path: FileArg,
    out: Annotated[Path, typer.Option("--out", "-o", help="Destination directory.")],
    as_json: JsonOpt = False,
) -> None:
    """Explode the archive into a directory, decompressed."""
    from ezf3d.container.archive import F3DArchive

    written: list[dict[str, Any]] = []
    directories = 0
    try:
        with F3DArchive(path) as archive:
            # Recreate empty directory entries too, so the result matches what
            # `unzip` produces byte for byte and path for path.
            for name in archive.directories():
                (out / name).mkdir(parents=True, exist_ok=True)
                directories += 1
            for entry in archive.entries():
                target = out / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(entry.name)
                target.write_bytes(data)
                written.append(
                    {"name": entry.name, "bytes": len(data), "method": entry.method_name}
                )
    except READ_ERRORS as exc:
        _fail("dump", path, exc, as_json)
        return

    payload = {
        "out": str(out),
        "files": len(written),
        "directories": directories,
        "bytes": sum(f["bytes"] for f in written),
        "entries": written,
    }
    if as_json:
        _emit("dump", path, payload, True)
        return
    console.print(
        f"wrote [bold]{payload['files']}[/] files "
        f"([bold]{_si(payload['bytes'])}[/]) to [cyan]{out}[/]"
    )


# -- thumb -----------------------------------------------------------------


@app.command()
def thumb(
    path: FileArg,
    out: Annotated[Path, typer.Option("--out", "-o", help="Destination PNG.")],
    as_json: JsonOpt = False,
) -> None:
    """Extract the design's embedded preview image."""
    try:
        with readfile(path) as doc:
            data = doc.preview()
            if data is None:
                raise Ef3dError("this document has no embedded preview")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
    except READ_ERRORS as exc:
        _fail("thumb", path, exc, as_json)
        return
    payload = {"out": str(out), "bytes": len(data)}
    if as_json:
        _emit("thumb", path, payload, True)
        return
    console.print(f"wrote [bold]{_si(len(data))}[/] to [cyan]{out}[/]")


# -- raw -------------------------------------------------------------------


@app.command()
def raw(
    path: FileArg,
    entry: Annotated[str | None, typer.Argument(help="Archive entry to inspect.")] = None,
    as_json: JsonOpt = False,
    mode: Annotated[str, typer.Option("--mode", help="auto | hex | strings | tokens")] = "auto",
    limit: Annotated[int, typer.Option("--limit", help="Rows of output.")] = 40,
) -> None:
    """Forensic view of any archive entry — hex, strings, or ASM tokens."""
    from ezf3d.container.archive import F3DArchive

    try:
        with F3DArchive(path) as archive:
            if entry is None:
                payload = [
                    {
                        "name": e.name,
                        "size": e.size,
                        "compressed": e.compressed_size,
                        "method": e.method_name,
                    }
                    for e in archive.entries()
                ]
                if as_json:
                    _emit("raw", path, payload, True)
                    return
                table = Table(header_style="bold", box=None, pad_edge=False)
                for column in ("entry", "size", "on disk", "method"):
                    table.add_column(column, overflow="fold")
                for row in payload:
                    table.add_row(
                        row["name"], _si(row["size"]), _si(row["compressed"]), row["method"]
                    )
                console.print(table)
                return

            data = archive.read(entry)
            chosen = mode
            if chosen == "auto":
                chosen = (
                    "tokens"
                    if entry.endswith((".smb", ".smbh"))
                    else "strings"
                    if entry.endswith(".dat")
                    else "hex"
                )
            payload = _raw_payload(entry, data, chosen, limit)
    except READ_ERRORS as exc:
        _fail("raw", path, exc, as_json)
        return

    if as_json:
        _emit("raw", path, payload, True)
        return
    console.print(f"[bold cyan]{entry}[/] [dim]{_si(len(data))} · {payload['mode']}[/]")
    for line in payload["lines"]:
        console.print(line, markup=False, highlight=False)


def _raw_payload(entry: str, data: bytes, mode: str, limit: int) -> dict[str, Any]:
    lines: list[str] = []
    if mode == "hex":
        for offset in range(0, min(len(data), limit * 16), 16):
            chunk = data[offset : offset + 16]
            text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            lines.append(f"{offset:08x}  {chunk.hex(' '):<47}  {text}")
    elif mode == "strings":
        for found in scan_strings(data, min_len=3):
            lines.append(f"{found.offset:8d}  {found.kind:5}  {found.value}")
            if len(lines) >= limit:
                break
    elif mode == "tokens":
        header = read_header(data)
        lines.append(
            f"header  {header.signature}  ASM {header.kernel_release}  "
            f"word={header.word_size}  resabs={header.resabs:g}  written={header.written}"
        )
        model = parse_asm(data)
        lines.append(f"entities {len(model)}  history={model.has_history}")
        for record in model.entities[:limit]:
            parts = []
            for tag, value in record.tokens[:10]:
                name = Tag(tag).name if tag in Tag._value2member_map_ else f"0x{tag:02X}"
                if isinstance(value, float):
                    value = round(value, 6)
                elif isinstance(value, tuple):
                    value = tuple(round(v, 4) for v in value)
                parts.append(name if value is None else f"{name}={value!r}")
            tail = " ..." if len(record.tokens) > 10 else ""
            lines.append(f"[{record.index:6d}] " + "  ".join(parts) + tail)
    else:
        raise Ef3dError(f"unknown --mode {mode!r}; use auto, hex, strings or tokens")
    return {"entry": entry, "bytes": len(data), "mode": mode, "lines": lines}


# -- render ----------------------------------------------------------------


@app.command()
def render(
    path: FileArg,
    out: Annotated[Path, typer.Option("--out", "-o", help="Destination PNG.")],
    as_json: JsonOpt = False,
    view: Annotated[
        str, typer.Option("--view", help="iso, front, back, left, right, top or bottom.")
    ] = "iso",
    body: Annotated[
        str | None, typer.Option("--body", help="Render one body by UUID prefix.")
    ] = None,
    size: Annotated[str, typer.Option("--size", help="Output size as WIDTHxHEIGHT.")] = "1024x768",
    tolerance: Annotated[
        float, typer.Option("--tolerance", help="Chord tolerance in cm.")
    ] = DEFAULT_CHORD_TOLERANCE,
    turntable: Annotated[
        int, typer.Option("--turntable", help="Tile this many orbiting views.")
    ] = 0,
    perspective: Annotated[
        bool, typer.Option("--perspective", help="Perspective instead of orthographic.")
    ] = False,
    chords: Annotated[
        bool,
        typer.Option("--chords", help="Draw not-yet-evaluable curves as straight chords."),
    ] = False,
    shaded: Annotated[
        bool, typer.Option("--shaded", help="Tessellate and render solid surfaces.")
    ] = False,
    source: SourceOpt = "asm",
) -> None:
    """Draw a wireframe of the design's edges to a PNG."""
    try:
        width, _, height = size.partition("x")
        pixels = (int(width), int(height))
    except ValueError:
        _fail("render", path, Ef3dError(f"bad --size {size!r}; expected WIDTHxHEIGHT"), as_json)
        return

    try:
        with readfile(path) as doc:
            cache = _resolve_source(doc, source, body, "render", path, as_json)
            if cache is not None:
                scene = Scene(segments=cache.segments, bodies=1, polylines=cache.edges)
                solid = cache.mesh if shaded else None
            else:
                scene = build_scene(doc, tolerance=tolerance, body=body, chords=chords)
                solid = build_mesh(doc, tolerance=tolerance, body=body).mesh if shaded else None
            if scene.is_empty and (solid is None or solid.is_empty):
                raise Ef3dError("nothing to draw: no evaluable geometry in this design")
            frames = max(turntable, 0)
            columns = 1 if frames <= 1 else min(frames, 3)
            tile = (
                (pixels[0] // columns, pixels[1] // max(1, (frames + columns - 1) // columns))
                if frames > 1
                else pixels
            )
            framing = solid.vertices if solid is not None and not solid.is_empty else scene.points()
            base = Camera.fit_points(
                framing,
                view=view,
                width=tile[0],
                height=tile[1],
                perspective=perspective,
            )

            def draw(camera: Camera):
                if solid is None:
                    return render_lines(scene.segments, camera)
                return render_mesh(solid, camera, edges=scene.segments)

            if frames > 1:
                step = 2 * math.pi / frames
                image = contact_sheet([draw(base.orbit(i * step)) for i in range(frames)], columns)
            else:
                image = draw(base)
            out.parent.mkdir(parents=True, exist_ok=True)
            written = png_write(out, image)
    except READ_ERRORS as exc:
        _fail("render", path, exc, as_json)
        return

    ink = ink_bounds(image, Style().background)
    payload = {
        "out": str(out),
        "bytes": written,
        "size": [image.shape[1], image.shape[0]],
        "view": view,
        "frames": max(turntable, 1),
        "bodies": scene.bodies,
        "polylines": scene.polylines,
        "segments": len(scene.segments),
        "chord_approximated": scene.approximated,
        "omitted": scene.omitted,
        "skipped": scene.skipped,
        "ink_bounds": list(ink) if ink else None,
        "shaded": shaded,
        "triangles": len(solid) if solid is not None else 0,
        "source": source,
    }
    if as_json:
        _emit("render", path, payload, True)
        return

    console.print(
        f"wrote [cyan]{out}[/] [dim]{image.shape[1]}x{image.shape[0]}, "
        f"{_si(written)}[/] · {scene.bodies} bodies, "
        f"{len(scene.segments):,} segments"
    )
    if scene.omitted:
        console.print(
            f"[yellow]{scene.omitted} edges omitted[/] [dim]— no approximating "
            f"spline, or one that misses the edge's own vertices; --chords "
            f"draws them as straight lines[/]"
        )
    if scene.approximated:
        console.print(
            f"[yellow]{scene.approximated} edges drawn as straight chords[/] "
            f"[dim]— approximate, not real geometry[/]"
        )


# -- mesh ------------------------------------------------------------------


def _tessellation_payload(result, tolerance: float) -> dict[str, Any]:
    mesh = result.mesh
    bounds = mesh.bounds()
    return {
        "tolerance_cm": tolerance,
        "triangles": len(mesh),
        "vertices": len(mesh.vertices),
        "faces_meshed": result.faces_meshed,
        "faces_skipped": result.faces_skipped,
        "unsupported": dict(sorted(result.unsupported.items())),
        "solids": result.solids,
        "closed_candidates": result.closed_candidates,
        "watertight_solids": result.watertight_solids,
        "max_deviation_cm": round(result.max_deviation, 9),
        "faces_over_tolerance": result.faces_over_tolerance,
        "area_cm2": round(mesh.area(), 6) if not mesh.is_empty else 0.0,
        "bounds_cm": (
            {"min": [round(v, 6) for v in bounds[0]], "max": [round(v, 6) for v in bounds[1]]}
            if bounds
            else None
        ),
    }


@app.command()
def mesh(
    path: FileArg,
    as_json: JsonOpt = False,
    body: Annotated[
        str | None, typer.Option("--body", help="Only this body, by UUID prefix.")
    ] = None,
    tolerance: Annotated[
        float, typer.Option("--tolerance", help="Chord tolerance in cm.")
    ] = DEFAULT_CHORD_TOLERANCE,
    source: SourceOpt = "asm",
) -> None:
    """Tessellate the design's faces, or read the triangles Fusion cached."""
    try:
        with readfile(path) as doc:
            cache = _resolve_source(doc, source, body, "mesh", path, as_json)
            if cache is not None:
                payload = _cache_payload(cache)
                if as_json:
                    _emit("mesh", path, payload, True)
                    return
                _print_cache(cache)
                return
            result = build_mesh(doc, tolerance=tolerance, body=body)
    except READ_ERRORS as exc:
        _fail("mesh", path, exc, as_json)
        return

    payload = _tessellation_payload(result, tolerance)
    if as_json:
        _emit("mesh", path, payload, True)
        return

    console.print(
        f"[bold]{payload['triangles']:,}[/] triangles from "
        f"[bold]{result.faces_meshed:,}[/] faces "
        f"[dim](chord tolerance {tolerance} cm, worst deviation "
        f"{result.max_deviation:.2e} cm)[/]"
    )
    console.print(
        f"[dim]solids[/] {result.solids}  "
        f"[dim]watertight[/] {result.watertight_solids}/{result.closed_candidates} "
        f"[dim]of those closed in the B-Rep and fully meshed[/]"
    )
    for reason, count in sorted(result.unsupported.items(), key=lambda item: -item[1]):
        console.print(f"[yellow]{count:5d}[/] faces skipped [dim]— {reason}[/]")


# -- export ----------------------------------------------------------------


@app.command()
def export(
    path: FileArg,
    out: Annotated[Path, typer.Option("--out", "-o", help="Destination file.")],
    as_json: JsonOpt = False,
    fmt: Annotated[
        str, typer.Option("--format", "-f", help=f"One of: {', '.join(FORMATS)}.")
    ] = "stl",
    body: Annotated[
        str | None, typer.Option("--body", help="Only this body, by UUID prefix.")
    ] = None,
    tolerance: Annotated[
        float, typer.Option("--tolerance", help="Chord tolerance in cm.")
    ] = DEFAULT_CHORD_TOLERANCE,
    unit: Annotated[str, typer.Option("--unit", help="Output unit: mm or cm.")] = "mm",
    source: SourceOpt = "asm",
) -> None:
    """Tessellate and write the mesh as STL, OBJ or glTF."""
    scale = CM_TO_MM if unit == "mm" else 1.0
    cache: CachedGeometry | None = None
    try:
        if unit not in ("mm", "cm"):
            raise ExportError(f"unknown --unit {unit!r}; expected mm or cm")
        with readfile(path) as doc:
            cache = _resolve_source(doc, source, body, "export", path, as_json)
            mesh = cache.mesh if cache is not None else None
            result = None if cache is not None else build_mesh(doc, tolerance=tolerance, body=body)
            if mesh is None and result is not None:
                mesh = result.mesh
            out.parent.mkdir(parents=True, exist_ok=True)
            written = write_mesh(mesh, out, fmt, scale=scale, name=doc.name)
    except READ_ERRORS as exc:
        _fail("export", path, exc, as_json)
        return

    payload = (
        _cache_payload(cache) if cache is not None else _tessellation_payload(result, tolerance)
    )
    payload.update({"out": str(out), "format": fmt, "bytes": written, "unit": unit})
    if as_json:
        _emit("export", path, payload, True)
        return
    console.print(
        f"wrote [cyan]{out}[/] [dim]{fmt}, {_si(written)}[/] · "
        f"{payload['triangles']:,} triangles in {unit}"
        + (" [dim]from Fusion's graphics cache[/]" if cache is not None else "")
    )
    if result is not None and result.faces_skipped:
        console.print(
            f"[yellow]{result.faces_skipped} faces missing[/] "
            f"[dim]— {', '.join(sorted(result.unsupported))}[/]"
        )


def _cache_payload(cache: CachedGeometry) -> dict[str, Any]:
    bounds = cache.mesh.bounds()
    return {
        "faces": cache.faces,
        "edges": cache.edges,
        "triangles": cache.triangles,
        "vertices": len(cache.mesh.vertices),
        "body": cache.body,
        "candidate_bodies": cache.candidates,
        "contributing_bodies": cache.contributors,
        "corner_coverage": round(cache.corner_coverage, 6),
        "body_faces": cache.body_faces,
        "covers_body": cache.covers_body,
        "watertight": cache.mesh.is_watertight,
        "blob_gap_bytes": cache.gap,
        "blob_overlap_bytes": cache.overlap,
        "bounds_cm": (
            {"min": [round(v, 6) for v in bounds[0]], "max": [round(v, 6) for v in bounds[1]]}
            if bounds
            else None
        ),
    }


def _resolve_source(
    doc: Document, source: str, body: str | None, command: str, path: Path, as_json: bool
) -> CachedGeometry | None:
    """The cached geometry to use, or ``None`` to tessellate instead.

    ``auto`` uses the cache only when it covers every face of the body it
    draws.  A partial cache is a fragment of the solid, and quietly exporting
    a third of a body as if it were the whole one is the failure this avoids.
    """
    if source not in SOURCES:
        _fail(
            command,
            path,
            Ef3dError(f"unknown --source {source!r}; expected one of {SOURCES}"),
            as_json,
        )
    if source == "asm":
        return None
    cache = build_cached(doc, body=body, identify=True)
    if cache is None or cache.is_empty:
        if source == "ogs":
            _fail(command, path, Ef3dError("this design carries no graphics cache"), as_json)
        return None
    if source == "auto" and not cache.covers_body:
        if not as_json:
            held = (
                f"{cache.faces} of {cache.body_faces} faces"
                if cache.body_faces
                else f"{cache.contributors} bodies at once"
            )
            err_console.print(
                f"[dim]graphics cache present but holds {held}; tessellating instead[/]"
            )
        return None
    return cache


@app.command()
def ogs(
    path: FileArg,
    as_json: JsonOpt = False,
    verify: Annotated[
        bool,
        typer.Option("--verify", help="Measure cached vertices against the ASM surfaces."),
    ] = False,
) -> None:
    """Report the OGS graphics cache — Fusion's own tessellation of the design."""
    try:
        with readfile(path) as doc:
            cache = build_cached(doc, identify=True)
            if cache is None:
                raise Ef3dError("this design carries no graphics cache")
            payload = _cache_payload(cache)
            if verify and not cache.candidates:
                # Nothing to check against: the cache draws several bodies, so
                # no single one owns its faces.  Comparing against all of them
                # would be slow and would not mean anything.
                payload["agreement"] = None
            elif verify:
                cached = open_cache(doc)
                report = None
                for item in chosen_bodies(doc, None):
                    if item.uuid not in cache.candidates:
                        continue
                    candidate = compare(cached, Shape(item.model()))
                    if report is None or candidate.matched > report[1].matched:
                        report = (item.uuid, candidate)
                if report is not None:
                    payload["verified_against"] = report[0]
                    payload["agreement"] = {
                        "matched": report[1].matched,
                        "unmatched": report[1].unmatched,
                        "unevaluated": report[1].unevaluated,
                        "by_surface": [
                            {
                                "surface": kind,
                                "faces": count,
                                "typical_cm": typical,
                                "worst_typical_cm": worst_typical,
                                "worst_cm": worst,
                            }
                            for kind, count, typical, worst_typical, worst in report[1].summary()
                        ],
                    }
    except READ_ERRORS as exc:
        _fail("ogs", path, exc, as_json)
        return

    if as_json:
        _emit("ogs", path, payload, True)
        return

    _print_cache(cache)
    if payload.get("agreement"):
        _print_agreement(payload)
    elif verify:
        console.print(
            "[dim]no single body owns these faces, so there is nothing to verify against[/]"
        )


def _print_cache(cache: CachedGeometry) -> None:
    console.print(
        f"[bold]{cache.triangles:,}[/] cached triangles over "
        f"[bold]{cache.faces:,}[/] faces and {cache.edges:,} edges"
    )
    if cache.body:
        state = (
            "the whole body" if cache.covers_body else f"{cache.faces} of {cache.body_faces} faces"
        )
        console.print(f"[dim]draws[/] {cache.body[:8]} [dim]—[/] {state}")
    elif cache.candidates:
        console.print(
            f"[yellow]several bodies match[/] [dim]{', '.join(u[:8] for u in cache.candidates)}; "
            f"holds {cache.faces} faces against {cache.body_faces} in the largest[/]"
        )
    elif cache.contributors > 1:
        console.print(
            f"[yellow]draws an assembly[/] [dim]— corners come from "
            f"{cache.contributors} bodies, so it belongs to no single one[/]"
        )
    elif cache.contributors:
        console.print("[yellow]no body accounts for the cached corners[/]")
    if cache.contributors:
        console.print(f"[dim]corners that are a B-Rep vertex[/] {cache.corner_coverage:.1%}")
    console.print(
        f"[dim]blob read whole[/] {'yes' if not (cache.gap or cache.overlap) else 'no'} "
        f"[dim]· welds to a closed manifold[/] {'yes' if cache.mesh.is_watertight else 'no'}"
    )


def _print_agreement(payload: dict[str, Any]) -> None:
    table = Table(box=None, pad_edge=False)
    for column in ("surface", "faces", "typical", "worst typical", "worst"):
        table.add_column(column, justify="left" if column == "surface" else "right")
    for row in payload["agreement"]["by_surface"]:
        table.add_row(
            row["surface"],
            f"{row['faces']}",
            f"{row['typical_cm']:.2e}",
            f"{row['worst_typical_cm']:.2e}",
            f"{row['worst_cm']:.2e}",
        )
    console.print(
        f"\n[dim]cached vertices against {payload['verified_against'][:8]}'s surfaces, "
        f"{payload['agreement']['matched']} faces paired, "
        f"{payload['agreement']['unmatched']} unpaired[/]"
    )
    console.print(table)


@app.command()
def components(
    path: FileArg,
    as_json: JsonOpt = False,
    features: Annotated[
        bool, typer.Option("--features", help="List each component's declared feature kinds.")
    ] = False,
    materials: Annotated[
        bool, typer.Option("--materials", help="Show each component's material and appearance.")
    ] = False,
) -> None:
    """The design's component tree, and which bodies each component owns."""
    try:
        with readfile(path) as doc:
            rows = design_infos(doc)
            if not rows:
                raise Ef3dError("this document carries no design segment")
    except READ_ERRORS as exc:
        _fail("components", path, exc, as_json)
        return

    payload = {"documents": [_dump(row) for row in rows]}
    if as_json:
        _emit("components", path, payload, True)
        return

    for row in rows:
        console.print(
            f"[bold]{row.document}[/] [dim]— {len(row.components)} components, "
            f"{row.objects:,} design objects[/]"
        )
        columns: list[tuple[str, str]] = [
            ("component", "left"),
            ("id", "right"),
            ("bodies", "right"),
            ("declares", "left"),
        ]
        if materials:
            columns += [("material", "left"), ("appearance", "left"), ("own", "right")]
        table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
        for column, justify in columns:
            table.add_column(column, justify=justify, overflow="fold")
        for component in row.components:
            kinds = len(component.declared_features)
            shown = (
                ", ".join(component.declared_features)
                if features
                else (f"{kinds} kinds" if kinds else "—")
            )
            label = component.name if component.named else f"[dim]{component.name[:8]}…[/]"
            cells = [label, str(component.oid), str(len(component.bodies)), shown]
            if materials:
                cells += [
                    _material_cell(component.material),
                    component.appearance or "[dim]—[/]",
                    str(component.body_materials),
                ]
            table.add_row(*cells)
        console.print(table)
        # The graph names its bodies by blob filename, so this is a real check
        # rather than a restatement: the two counts come from different files.
        mark = "[green]" if row.bodies_named == row.bodies_on_disk else "[yellow]"
        console.print(
            f"{mark}{row.bodies_named}[/] bodies named by the graph, "
            f"{row.bodies_on_disk} in Breps.BlobParts"
        )
        if row.assignments:
            # The ids come from the design stream and the categories from the
            # nested .protein package, so their agreement is a real check.
            mark = "[green]" if not row.undeclared_assets else "[yellow]"
            console.print(
                f"{row.assignments} material assignments over "
                f"{len(row.material_assets)} assets, {mark}"
                f"{len(row.material_assets) - len(row.undeclared_assets)}[/] of them "
                f"declared by the .protein package"
            )
        console.print()


@app.command()
def params(
    path: FileArg,
    as_json: JsonOpt = False,
    component: Annotated[
        str | None,
        typer.Option("--component", help="Only parameters of components whose name contains this."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Rows to print per document; 0 for all.")
    ] = 40,
) -> None:
    """The design's parameters: name, role, unit, expression and value."""
    try:
        with readfile(path) as doc:
            rows = parameter_infos(doc)
            if not rows:
                raise Ef3dError("this document carries no design segment")
    except READ_ERRORS as exc:
        _fail("params", path, exc, as_json)
        return

    payload = {"documents": [_dump(row) for row in rows]}
    if as_json:
        _emit("params", path, payload, True)
        return

    for row in rows:
        chosen = [
            parameter
            for parameter in row.parameters
            if component is None or component.lower() in parameter.component.lower()
        ]
        console.print(
            f"[bold]{row.document}[/] [dim]— {row.declared} parameters"
            + (f", {len(chosen)} shown" if len(chosen) != row.declared else "")
            + "[/]"
        )
        if not chosen:
            console.print("[dim]no parameters[/]\n")
            continue

        table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
        for column, justify in (
            ("component", "left"),
            ("name", "left"),
            ("role", "left"),
            ("expression", "left"),
            ("value", "right"),
            ("unit", "left"),
        ):
            table.add_column(column, justify=justify, overflow="fold")
        shown = chosen if limit <= 0 else chosen[:limit]
        for parameter in shown:
            value = "[dim]?[/]" if parameter.display is None else f"{parameter.display:g}"
            table.add_row(
                parameter.component,
                parameter.name,
                parameter.role,
                parameter.expression,
                value,
                parameter.unit or "[dim]—[/]",
            )
        console.print(table)
        if len(shown) < len(chosen):
            console.print(f"[dim]… {len(chosen) - len(shown)} more (--limit 0 for all)[/]")

        # Two independent fields agreeing is what says the values are read in
        # the right units, so it is reported rather than assumed.
        mark = "[green]" if not row.literals_disagreeing else "[yellow]"
        console.print(
            f"{mark}{row.literals_checked - len(row.literals_disagreeing)}[/] of "
            f"{row.literals_checked} literal expressions agree with their stored value"
        )
        if row.unreadable:
            console.print(f"[yellow]{len(row.unreadable)}[/] declared names did not read")
        console.print()


@app.command()
def timeline(
    path: FileArg,
    as_json: JsonOpt = False,
    kinds: Annotated[
        bool, typer.Option("--kinds", help="Show the per-kind census against the registry.")
    ] = False,
    inputs: Annotated[
        bool, typer.Option("--inputs", help="Show the numbers that drive each feature.")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", help="Rows to print per document; 0 for all.")
    ] = 40,
) -> None:
    """The design's features, in the order the timeline runs them."""
    try:
        with readfile(path) as doc:
            rows = timeline_infos(doc)
            if not rows:
                raise Ef3dError("this document carries no design segment")
    except READ_ERRORS as exc:
        _fail("timeline", path, exc, as_json)
        return

    payload = {"documents": [_dump(row) for row in rows]}
    if as_json:
        _emit("timeline", path, payload, True)
        return

    for row in rows:
        issued = sum(row.declared.values())
        console.print(
            f"[bold]{row.document}[/] [dim]— {len(row.entries)} features"
            + (f", {issued} ever issued" if issued else "")
            + "[/]"
        )
        if not row.entries:
            console.print("[dim]no timeline[/]\n")
            continue

        if kinds:
            _print_kinds(row)
        else:
            columns = [("#", "right"), ("feature", "left"), ("kind", "left")]
            columns += (
                [("does", "left"), ("drives", "left")]
                if inputs
                else [("component", "left"), ("id", "right"), ("inputs", "right")]
            )
            table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
            for column, justify in columns:
                table.add_column(column, justify=justify, overflow="fold")
            shown = row.entries if limit <= 0 else row.entries[:limit]
            for entry in shown:
                cells = [
                    str(entry.index + 1),
                    entry.name or "[dim]unnamed[/]",
                    entry.kind or "[dim]—[/]",
                ]
                if inputs:
                    does = " · ".join(x for x in (entry.operation, entry.direction) if x)
                    cells += [does or "[dim]—[/]", _drives(entry)]
                else:
                    cells += [entry.component, str(entry.oid), str(entry.inputs)]
                table.add_row(*cells)
            console.print(table)
            if len(shown) < len(row.entries):
                console.print(f"[dim]… {len(row.entries) - len(shown)} more (--limit 0 for all)[/]")

        # Both checks come from the registry, which is written separately from
        # the list, so agreement is evidence rather than a restatement.
        if row.unknown_labels:
            console.print(f"[yellow]{len(row.unknown_labels)}[/] labels no registry declares")
        for line in row.over_counter:
            console.print(f"[yellow]more live than ever issued[/] — {line}")
        if not row.unknown_labels and not row.over_counter:
            console.print(
                "[green]every[/] feature's kind is one the registry declares, "
                "and none outruns its counter"
            )
        if inputs:
            driven = sum(1 for entry in row.entries if entry.parameters)
            console.print(
                f"[green]{driven}[/] of {len(row.entries)} features carry at least one "
                f"parameter; the rest are kinds with no number to carry"
            )
        if row.unnamed:
            console.print(f"[dim]{row.unnamed} entries carry no label[/]")
        # Named feature records the list does not hold.  Said out loud because
        # a timeline that quietly drops a design's joints reads as complete.
        loose = sum(row.outside.values())
        if loose:
            top = ", ".join(
                f"{k} {n}" for k, n in sorted(row.outside.items(), key=lambda kv: -kv[1])[:4]
            )
            console.print(f"[dim]{loose} named features sit outside the list — {top}[/]")
        console.print()


@app.command()
def sketches(
    path: FileArg,
    as_json: JsonOpt = False,
    points: Annotated[
        bool, typer.Option("--points", help="List every point of every sketch shown.")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", help="Rows to print per document; 0 for all.")
    ] = 40,
) -> None:
    """The design's sketches: their points, curves and driving dimensions."""
    try:
        with readfile(path) as doc:
            rows = sketch_infos(doc)
            if not rows:
                raise Ef3dError("this document carries no design segment")
    except READ_ERRORS as exc:
        _fail("sketches", path, exc, as_json)
        return

    payload = {"documents": [_dump(row) for row in rows]}
    if as_json:
        _emit("sketches", path, payload, True)
        return

    for row in rows:
        console.print(
            f"[bold]{row.document}[/] [dim]— {len(row.sketches)} sketches, "
            f"{row.points} points, {row.curves} curves[/]"
        )
        if not row.sketches:
            # A registry-less `.f3z` member has entity records and no feature
            # object to own them.  Say so rather than reporting an empty design.
            console.print("[dim]no sketches[/]")
            if row.unowned:
                console.print(
                    f"[yellow]{row.unowned}[/] entity records, but no sketch object to own them"
                )
            console.print()
            continue

        table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
        for column, justify in (
            ("#", "right"),
            ("sketch", "left"),
            ("component", "left"),
            ("id", "right"),
            ("points", "right"),
            ("curves", "right"),
            ("extent cm", "left"),
            ("dims", "right"),
        ):
            table.add_column(column, justify=justify, overflow="fold")
        shown = row.sketches if limit <= 0 else row.sketches[:limit]
        for sketch in shown:
            table.add_row(
                str(sketch.index + 1) if sketch.index >= 0 else "[dim]—[/]",
                sketch.name or "[dim]unnamed[/]",
                sketch.component,
                str(sketch.oid),
                str(sketch.points),
                str(sketch.curves),
                _extent_cell(sketch.extent),
                _dimension_cell(sketch),
            )
        console.print(table)
        if len(shown) < len(row.sketches):
            console.print(f"[dim]… {len(row.sketches) - len(shown)} more (--limit 0 for all)[/]")

        if points:
            for sketch in shown:
                if not sketch.coordinates:
                    continue
                drawn = "  ".join(f"({x:g}, {y:g})" for x, y in sketch.coordinates)
                console.print(f"[dim]  {sketch.oid}:[/] {drawn}")

        # The parameters are read from the table and the points from the
        # geometry records, so agreement is a cross-check rather than a
        # restatement of one reading.
        checked = sum(sketch.dimensions_checked for sketch in row.sketches)
        missing = sum(len(sketch.dimensions_missing) for sketch in row.sketches)
        if checked or missing:
            console.print(
                f"[green]{checked}[/] of {checked + missing} linear dimensions are a distance "
                f"between two of their own sketch's points"
            )
            if missing:
                console.print(
                    f"[dim]{missing} measure a point against a line, which is not read yet[/]"
                )
        loose = sum(1 for sketch in row.sketches if sketch.index < 0)
        if loose:
            console.print(f"[dim]{loose} sketches sit outside the timeline[/]")
        if row.unowned:
            console.print(f"[yellow]{row.unowned}[/] entity records name no sketch")
        console.print()


def _dimension_cell(sketch: Any) -> str:
    """Linear dimensions re-derived from this sketch's own points, over the total."""
    total = sketch.dimensions_checked + len(sketch.dimensions_missing)
    if not total:
        return "[dim]—[/]"
    colour = "green" if not sketch.dimensions_missing else "yellow"
    return f"[{colour}]{sketch.dimensions_checked}[/]/{total}"


def _extent_cell(extent: list[float]) -> str:
    """A sketch's bounding box as ``w x h``, or a dash when it has no points."""
    if len(extent) != 4:
        return "[dim]—[/]"
    xmin, ymin, xmax, ymax = extent
    if xmax == xmin and ymax == ymin:
        return "[dim]—[/]"
    return f"{xmax - xmin:g} x {ymax - ymin:g}"


def _material_cell(material: str) -> str:
    """A material reads as a guid when only the ``.protein`` package names it."""
    if not material:
        return "[dim]—[/]"
    return material if "|" in material else f"[dim]{material[:8]}…[/]"


def _drives(entry: Any) -> str:
    """A feature's parameters as ``role=expression``, or a dash when it has none.

    A dash is not a failure: PasteBodies, DeleteBody and Combine carry no
    number, which is why the coverage figure is 70% and not higher.
    """
    if not entry.parameters:
        return "[dim]—[/]"
    return ", ".join(f"{p.role}=[bold]{p.expression or p.display}[/]" for p in entry.parameters)


def _print_kinds(row: Any) -> None:
    table = Table(box=None, pad_edge=False, show_header=True, header_style="bold")
    for column, justify in (("kind", "left"), ("live", "right"), ("issued", "right")):
        table.add_column(column, justify=justify, overflow="fold")
    for kind in sorted(set(row.declared) | set(row.census)):
        live = row.census.get(kind, 0)
        issued = row.declared.get(kind, 0)
        table.add_row(kind, str(live) if live else "[dim]0[/]", str(issued))
    console.print(table)


# -- version ---------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", help="Print the ezf3d version and exit.")
    ] = False,
) -> None:
    if version:
        console.print(f"ezf3d {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


def run() -> None:  # pragma: no cover - console-script shim
    sys.exit(app())


if __name__ == "__main__":  # pragma: no cover
    app()
