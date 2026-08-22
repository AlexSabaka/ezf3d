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
from ezf3d.asm.header import AsmError, read_header
from ezf3d.asm.records import parse as parse_asm
from ezf3d.asm.tokens import Tag
from ezf3d.container.archive import UnsupportedCompressionError
from ezf3d.inspect import body_infos, document_info
from ezf3d.mesh.polyline import DEFAULT_CHORD_TOLERANCE
from ezf3d.model.document import Document, Ef3dError, readfile
from ezf3d.model.report import Envelope
from ezf3d.render.camera import Camera
from ezf3d.render.png import write as png_write
from ezf3d.render.raster import Style, ink_bounds, render_lines
from ezf3d.render.scene import build_scene, contact_sheet
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

#: Failures that mean "this file is not what we expected", not a crash.
READ_ERRORS = (Ef3dError, StreamError, AsmError, UnsupportedCompressionError, KeyError, OSError)


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
        for column in ("segment", "type", "ver", "meta", "bulk", "timeline"):
            table.add_column(column, overflow="fold")
        for segment in asset.segments:
            features = ", ".join(f"{k}×{v}" for k, v in list(segment.feature_types.items())[:6])
            if len(segment.feature_types) > 6:
                features += f", +{len(segment.feature_types) - 6} more"
            table.add_row(
                segment.name,
                segment.type,
                segment.version,
                _si(segment.meta_size),
                _si(segment.bulk_size),
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
            scene = build_scene(doc, tolerance=tolerance, body=body, chords=chords)
            if scene.is_empty:
                raise Ef3dError("nothing to draw: no evaluable edges in this design")
            frames = max(turntable, 0)
            columns = 1 if frames <= 1 else min(frames, 3)
            tile = (
                (pixels[0] // columns, pixels[1] // max(1, (frames + columns - 1) // columns))
                if frames > 1
                else pixels
            )
            base = Camera.fit_points(
                scene.points(),
                view=view,
                width=tile[0],
                height=tile[1],
                perspective=perspective,
            )
            if frames > 1:
                step = 2 * math.pi / frames
                image = contact_sheet(
                    [render_lines(scene.segments, base.orbit(i * step)) for i in range(frames)],
                    columns,
                )
            else:
                image = render_lines(scene.segments, base)
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
        "unplaced": scene.unplaced,
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
            f"[yellow]{scene.omitted} edges omitted[/] [dim]— spline curves land in "
            f"Phase 2.4; --chords draws them as straight lines[/]"
        )
    if scene.approximated:
        console.print(
            f"[yellow]{scene.approximated} edges drawn as straight chords[/] "
            f"[dim]— approximate, not real geometry[/]"
        )
    if scene.unplaced:
        console.print(
            "[yellow]bodies are drawn in their own local coordinates[/] [dim]— component "
            "placement lives in the design segment (Phase 3); use --body for one part[/]"
        )


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
