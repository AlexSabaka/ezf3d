# ezf3d — headless Fusion 360

Read Autodesk Fusion 360 `.f3d` / `.f3z` designs **without running Fusion**. A
pip-installable Python library and CLI, in the spirit of
[`ezdxf`](https://github.com/mozman/ezdxf) — `ezf3d.readfile(path)` and you're in.

**Status:** alpha. Reading, inspection, B-Rep traversal, analytic geometry and wireframe
rendering work today. Face tessellation, spline evaluation, feature-graph transpilation
and simulation are on the roadmap below.

Exercised against four real designs: 42 B-Rep bodies, 99.8 MB of Shape Manager data,
every file walked to its terminator with no unknown tokens. The geometry layer is checked
against the format's own redundancy — for every edge reachable from a body with ordinary
topology, the vertex must lie on the curve the edge names. Over **95,668 endpoints the
worst miss is 2.2e-07 cm**, well inside the kernel's own tolerance.

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
```

Every command takes `--json` for machine consumption.

```python
import ezf3d

with ezf3d.readfile("Design.f3d") as doc:
    doc.manifest.doc_type  # 'Fusion Document'
    doc.design.bulk.feature_types()  # Counter({'ExtrudeFeature': 9, 'Sketch': 8, ...})
    body = doc.bodies[0]  # nothing parsed yet - bodies load lazily
    body.model().header.kernel_release  # '232.4.0.65535'
    body.census().faces  # 2006
    body.census().analytic_only  # True -> tessellable without a spline kernel
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
- **Phase 2.3–2.5 — solids.** Face tessellation with STL/OBJ/glTF export, spline
  (`nubs`/`nurbs`) evaluation, and the OGS cached-mesh fast path.
- **Phase 3 — design semantics.** Parameters, sketches, feature timeline, component
  tree, joints, materials.
- **Phase 4 — transpile.** Fusion feature graph → `build123d` source → headless OCC
  regeneration, verified by geometric diff against the original bodies.
- **Phase 5 — simulate.** Mass properties and interference first, then `scikit-fem`
  linear static / modal / thermal.

Writing modified geometry back into `.f3d` is an explicit non-goal; headless iteration
happens through the transpile path.

## License

MIT. See [LICENSE](LICENSE).
