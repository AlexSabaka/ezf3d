# The OGS graphics cache

**Status: decoded, and used.** `ezf3d ogs` reports it; `--source ogs` meshes, exports and
renders from it.

Fusion may leave the display mesh it was drawing in the document, under
`<Asset>[Active]/OGS.BlobFolder/OGS/DefaultScene/`. OGS is Autodesk's *One Graphics
System*.

```
world              the scene graph, UTF-16LE, byte-aligned
Fusion_mesh_000    every body buffer, back to back, no header
stream_mesh_000    the origin planes and axes, same encoding
```

Three of the four samples carry one. It is optional, so it is a fast path and never the
only one.

## Object framing

`world` opens with the wide string `ARenderList` and is then a flat byte stream with no
alignment and no type tags. Objects are found by their header: a `0x01` flag byte
followed by a length-prefixed UTF-16LE name.

```
01  0d 00 00 00  "OgsSimpleMesh"     <- flag, u32 char count, UTF-16LE
```

The name is a class near the root (`GroupNode`, `VertexFormat`, `AIndexBuffer`) and a
node name deeper in (`Body`, `Faces`, `Face`, `Edges`, `Edge`). Nothing on the wire
distinguishes the two, and for reading geometry nothing needs to.

Scanning for that header is a heuristic. What makes it trustworthy rather than merely
plausible is stated below: the descriptors it finds tile the vertex blob exactly.

## Buffer descriptors

A node that owns geometry carries a fixed descriptor **45 bytes past the end of its
name**:

| Offset | Field | |
|---|---|---|
| +45 | `u32` | `0x800` when a buffer follows; `0` on a node that refers back |
| +49 | `u32` | kind — `7` triangles, `6` polyline |
| +53 | `u32` | zero in every sample — most likely which blob, of the one there is |
| +57 | `u32` | byte offset into `Fusion_mesh_000` |
| +61 | `u32` | position floats — 3 per vertex |
| +65 | `u32` | normal floats — 3 per vertex |
| +69 | `u32` | texture floats — 2 per vertex — *kind 7 only* |
| +73 | `u32` | index count — 3 per triangle — *kind 7 only* |
| +77 | `u32` `n`, then `n` × `u32` | the edges bounding this face — *kind 7 only* |
| | 6 × `f64` | the node's bounding box, in kernel centimetres |

So a face is `position / 3` vertices of interleaved `P3 N3 T2` float32 — 32 bytes each —
immediately followed by its `uint32` indices; an edge is `P3 N3`, 24 bytes each, with no
indices. Both live at the stated offset in `Fusion_mesh_000`.

The counts are per attribute rather than per vertex, which is why a face of four vertices
reads `12, 12, 8, 6`: twelve position floats, twelve normal floats, eight texture floats,
six indices.

### The same node is written more than once

A `world` holds several render lists over one body. Only the **first** `Faces` and
`Edges` group carries buffers; later copies set the flag word to zero and refer back.
Reading every flagged node and no others is what makes the descriptors tile:

| Sample | buffers | blob | gap | overlap |
|---|---|---|---|---|
| Mk1 Focuser, Wheel 2 | 1,429 | 758,832 B | 0 | 0 |
| SUCKER | 2,202 | 1,198,460 B | 0 | 0 |
| Focuser Mk1 (`.f3z`) | 7,694 | 3,697,812 B | 0 | 0 |

Every byte of vertex data is claimed by exactly one node. A missed node would leave a
gap and an invented one an overlap.

`stream_mesh_000` is described the same way but by `VertexFormat` objects, which carry
`(count, [buffer, offset, length])` triples directly. Its ten meshes tile its 860 bytes
exactly, ending at 800 + 60. It holds only the origin point, axes and planes.

## Which body it draws

`world` never names a BREP UUID. It is identified from geometry instead, and the key is
the cached **edges**: each is a polyline between two B-Rep vertices, so its endpoints are
`point` records of whatever is being drawn.

**Every cached corner is a B-Rep vertex of the document — 100.0 %, in every sample.**
That single fact settles the units (centimetres), the coordinate frame (shared with ASM,
no transform), and the correctness of the whole descriptor reading at once.

Neither face count nor bounding box works. Two bodies can have the same face count, and
a body of revolution bulges well past its own vertices — SUCKER's funnel reaches 3.39 cm
in *y* where its vertices stop at 2.30.

More than one body can score highly, and ezf3d says so rather than picking:

| Sample | cache holds | verdict |
|---|---|---|
| Mk1 Focuser, Wheel 2 | 423 faces, 1,006 edges | body `068db28d`, all 423 of its faces |
| SUCKER | 608 faces, 1,579 edges | two bodies match; 608 of 2,006 faces — **partial** |
| Focuser Mk1 | 2,123 faces, 5,292 edges | draws **ten bodies**; belongs to none |

A design saved with rollback history holds a body whose `point` records are a superset of
the plain body's, so both match. An assembly's cache draws many bodies at once.

## What it is worth

**As geometry.** Fusion's mesh is wound outwards, welds shut, and covers the spline
surfaces ezf3d declines to tessellate. On the wheel it is 14,984 triangles against
ezf3d's 5,983, read in 0.03 s against 2.0 s to tessellate — and reading it needs no ASM
parse at all unless you ask which body it draws.

`--source auto` uses it **only when it covers every face of the body it draws**. A
partial cache is a fragment of a solid, and exporting a third of a part as though it were
whole is the failure worth avoiding. `--source ogs` takes it regardless and says what it
holds.

**As evidence.** This is the larger part. The cache is Autodesk's tessellator against
ezf3d's reading of the surface equations, and comparing them tests both without going
near ezf3d's own triangulation:

| Surface | faces | typical | worst face |
|---|---|---|---|
| Plane | 120 | 5.2e-08 cm | 1.5e-07 |
| Sphere | 12 | 7.2e-08 | 7.5e-08 |
| Torus | 57 | 7.3e-08 | 1.4e-07 |
| Cone | 193 | 8.5e-08 | 6.5e-06 |
| **SplineSurface** | 26 | **2.3e-02** | **5.3e-01** |

(the wheel; SUCKER agrees, at 3.7e-08 for cones and 2.7e-03 for splines)

Analytic surfaces agree at float32 noise — the cache stores single precision, so that is
as close as two independent computations can come. Spline surfaces are out by five orders
of magnitude more, which is how the identification problem in
[unknowns.md](unknowns.md#spline-surface-identification) stopped being a suspicion.

Per face the figure quoted is the **median** over its vertices, not the worst. A face's
interior vertices test its surface; its boundary vertices sit on a trimming curve shared
with a neighbour, and an intersection curve is an approximation. On one cylinder five of
six vertices are exact to 1e-07 cm and the sixth is 1.2e-02 out — through no fault of the
cylinder.

### It found a bug

Rendering the wheel from the cache showed a three-spoke handwheel where ezf3d's own
tessellation showed a solid disc. Bridging a hole into its outer loop repeats two
vertices; an ear-clipping containment test that went by index found those duplicates on
every candidate ear, rejected all of them, and fell back to a fan across the whole
outline. The wheel's surface area came out at 2,003 cm² where its meshable faces come to
718, and 36 of Robotic_Bhujha's 116 closed solids were left non-manifold.

Nothing else in the suite could see it. Filled-in triangles lie on the plane like any
other, so the deviation check passed; they close the surface, so watertightness passed;
there were the same number of them, so a triangle count passed. Area is what gives it
away, and `tessellate_face` now checks it on every multi-loop face.

## Not read

- **`GraphicsBlobFeatures`, `NodeData`, attribute nodes.** Colour, visibility, transforms
  and line styles. Unread because ezf3d shades from geometry.
- **`FatPointMesh`, `SketchCurve`, `PrimitiveLODGeometryNode`.** Sketch display geometry,
  which has its own encodings. A handful of `SketchCurve` records — 13 in SUCKER, 108 in
  the `.f3z` — happen to carry `0x800` at the descriptor offset and then end before a
  descriptor would fit. They are counted as unread rather than skipped silently, but they
  are coincidence rather than missed geometry: nothing follows to read, and the face and
  edge buffers already tile the blob exactly without them.
- **`stream_mesh_000`.** The origin point, axes and planes, described by `VertexFormat`
  objects carrying `(count, [buffer, offset, length])` triples rather than the descriptor
  above. Decoded far enough to confirm the encoding and the exact tiling; not read,
  because construction geometry is not a body.
- **The opening node list.** `ARenderList`, a version word, then names with counts. It is
  a nested node list rather than a flat registry and its counts do not match what the
  stream holds, so ezf3d counts objects instead of trusting it.
