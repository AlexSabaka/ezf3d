# ezf3d — headless Fusion 360

Read Autodesk Fusion 360 `.f3d` / `.f3z` designs **without running Fusion**. A
pip-installable Python library and CLI, in the spirit of
[`ezdxf`](https://github.com/mozman/ezdxf) — `ezf3d.readfile(path)` and you're in.

**Status:** alpha. Reading, inspection, B-Rep traversal, analytic geometry, spline
curves, tessellation, mesh export, offscreen rendering and Fusion's own cached display
mesh all work today. Spline *surfaces*, feature-graph transpilation and simulation are on
the roadmap below.

Exercised against four real designs: 42 B-Rep bodies, 99.8 MB of Shape Manager data,
every file walked to its terminator with no unknown tokens. The geometry layer is checked
against the format's own redundancy — for every edge reachable from a body with ordinary
topology, the vertex must lie on the curve the edge names. Over **95,668 endpoints the
worst miss is 2.2e-07 cm**, well inside the kernel's own tolerance.

Where a design carries Fusion's own tessellation, that becomes a second, independent
check: cached vertices sit within **1e-07 cm** of every analytic surface ezf3d reads, and
every one of the cache's edge polylines ends on a B-Rep vertex.

## Why this exists

Fusion 360 has no headless mode — the only automation surface is a Python add-in that
runs inside a live Fusion instance. The one open project that touches `.f3d` at all,
[jmplonka/InventorLoader](https://github.com/jmplonka/InventorLoader), is a GPL-2.0
FreeCAD workbench: it can't run headless, it skips the design/feature streams entirely,
and it predates Fusion's switch to Zstandard-compressed ZIP entries, so it fails
outright on files saved today.

`ezf3d` is a clean-room MIT implementation of the format.

## Install

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
```

## Quick reference

```
ezf3d info    <file>              # doc type, versions, schema table, segments, size breakdown
ezf3d tree    <file>              # asset folders, segments, BREP inventory (.f3z: XREF graph)
ezf3d bodies  <file>              # per-body ASM topology census + geometry histogram
ezf3d dump    <file> --out <dir>  # explode the archive, decompressed
ezf3d thumb   <file> --out <png>  # extract the embedded preview
ezf3d raw     <file> <entry>      # forensic token/hex dump of any stream
ezf3d render  <file> --out <png>  # wireframe or --shaded, six views plus iso, --turntable
ezf3d mesh    <file>              # tessellate and report coverage, deviation, watertightness
ezf3d export  <file> --out <stl>  # STL, OBJ, glTF, GLB
ezf3d ogs     <file> [--verify]   # what Fusion cached, and how far it agrees with the B-Rep
```

`mesh`, `export` and `render` take `--source asm | ogs | auto`: tessellate the surfaces,
read Fusion's cached mesh, or use the cache when it covers the whole body and tessellate
otherwise.

Every command takes `--json` for machine consumption.

```python
import ezf3d

with ezf3d.readfile("Design.f3d") as doc:
    doc.manifest.doc_type  # 'Fusion Document'
    doc.design.bulk.declared_feature_types()  # {'ExtrudeFeature', 'Sketch', 'LoftFeature', ...}
    body = doc.bodies[0]  # nothing parsed yet - bodies load lazily
    body.model().header.kernel_release  # '232.4.0.65535'
    body.census().faces  # 2006
    body.census().analytic_only  # True -> tessellable without a spline kernel
    len(doc.design.objects())  # 3444 -> design objects, each with an offset and extent
```

`.f3z` packages resolve their reference graph: `readfile` returns the root design, with
`doc.linked` holding the XREF'd documents and `doc.package` the graph itself.

## The format, briefly

`.f3d` is a ZIP whose entries are **Zstandard**-compressed (method 93 — stdlib
`zipfile` cannot open them). Inside:

| Path | Contents |
|---|---|
| `Manifest.dat` | doc type, GUIDs, `{module: schema_version}` table |
| `<Asset>[Active]/<Segment>/{Meta,Bulk}Stream.dat` | typed object graph — the parametric timeline |
| `<Asset>[Active]/Breps.BlobParts/*.smb`, `*.smbh` | `ASM BinaryFile8` — Autodesk Shape Manager B-Rep (`.smbh` carries rollback history) |
| `<Asset>[Active]/OGS.BlobFolder/…` | One Graphics scene graph + pre-tessellated display mesh |
| `<Asset>[Active]/ProteinAssets.BlobParts/*.protein` | nested ZIP — Autodesk Protein materials |
| `<Asset>[Active]/Previews/small.png` | thumbnail |

Full notes live in [`docs/format/`](docs/format/).

## Roadmap

- **Phase 1 — container & inspection.** ✅
- **Phase 2.1 — geometry & traversal.** ✅ typed B-Rep walking and analytic curve and
  surface evaluation.
- **Phase 2.2 — wireframe render.** ✅ adaptive edge discretisation and a pure-numpy
  offscreen rasteriser.
- **Phase 2.3 — tessellation & export.** ✅ trimmed analytic faces, shaded rendering,
  STL/OBJ/glTF.
- **Phase 2.4 — splines.** ✅ for curves: `nubs`/`nurbs` reading, de Boor evaluation, and
  the interning table. Spline *surfaces* are read but not yet trusted — see
  [docs/format/unknowns.md](docs/format/unknowns.md).
- **Phase 2.5 — the OGS cached-mesh fast path.** ✅ the scene graph and buffer
  descriptors, cross-validated against the ASM tessellation — which is how a
  hole-triangulation bug and a stale-loop bug were found.
- **Phase 3 — design semantics.** Parameters, sketches, feature timeline, component
  tree, joints, materials. In progress: the meta stream is decoded, and its object index
  makes the design payload randomly addressable — 14,843 objects in one sample, each
  with a known offset and extent.
- **Phase 4 — transpile.** Fusion feature graph → `build123d` source → headless OCC
  regeneration, verified by geometric diff against the original bodies.
- **Phase 5 — simulate.** Mass properties and interference first, then `scikit-fem`
  linear static / modal / thermal.

Writing modified geometry back into `.f3d` is an explicit non-goal; headless iteration
happens through the transpile path.

## Development

```bash
uv run pytest                  # everything, ~11 min over 100 MB of sample CAD
uv run pytest -m "not slow"    # the inner loop, ~3.5 min
uv run ruff check . && uv run ruff format --check .
```

Tests run against real designs rather than fixtures, and check the format against its own
internal redundancy — a curve evaluated at its edge's parameter must reach the vertex, a
face's triangles must lie on the surface the face names, a face's mesh must cover its
outer loop less its holes. The `slow` marker covers the exhaustive sweeps that walk every
face of every body; the expensive results those sweeps share — parsed documents,
per-face tessellations, cache comparisons — are computed once per sample and reused.

## License

MIT. See [LICENSE](LICENSE).
