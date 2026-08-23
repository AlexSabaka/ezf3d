# `ASM BinaryFile` — Shape Manager bodies

Fusion's solids live in `Breps.BlobParts/BREP.<uuid>.smb`. Autodesk Shape Manager is a
fork of ACIS 7.0, so the container is a SAB (Standard ACIS Binary) stream carrying
Autodesk's entity vocabulary.

`.smb` is a plain body. **`.smbh` additionally carries an ASM rollback-history section**,
closed by an `End of ASM History Section` marker; such files also carry
`Timestamp_attrib_def` attributes.

## Header

```
b"ASM BinaryFile<N>"     15 bytes, no terminator; N is the pointer width
<N> x 4 little-endian    version, reserved, and two counts of unclear role
0x07 str  product        "Autodesk Neutron"
0x07 str  kernel         "ASM 232.4.0.65535 OSX"
0x07 str  written        "Sat Aug 22 17:33:12 2026"
0x06 f64  sizebox        model extent hint
0x06 f64  resabs         absolute tolerance, 1e-6
0x06 f64  resnor         normal tolerance, 1e-10
```

The trailing digit of the signature is the **pointer width in bytes**. Both are in the
wild: ASM 232 writes `ASM BinaryFile8` with 64-bit integers and pointers; ASM 231 and
earlier write `ASM BinaryFile4` with 32-bit. Stock ACIS SAB (`ACIS BinaryFile`) is
32-bit and parses with the same code. The version word tracks the release — the samples
alone span three: `23200` (18 bodies), `23100` (22) and `22700` (2).

The three strings and three doubles are ordinary SAB tokens, so the header is really a
fixed numeric prelude followed by the first record.

## Tokens

| Tag | Meaning | Payload |
|---|---|---|
| `0x00` | no type | — |
| `0x01` | byte | 1 |
| `0x02` | char | 1 |
| `0x03` | short | 2 |
| `0x04` | int | word size |
| `0x05` | float | 4 |
| `0x06` | double | 8 |
| `0x07` | string | `u8` length prefix |
| `0x08` / `0x09` | string | `u16` / `u32` length prefix |
| `0x0A` / `0x0B` | false / true | — |
| `0x0C` | pointer | word size |
| `0x0D` | entity type | `u8` length prefix |
| `0x0E` | derived entity type | `u8` length prefix |
| `0x0F` | subtype start | — |
| `0x10` | subtype end | — |
| `0x11` | end of record | — |
| `0x12` | literal string | `u32` length prefix |
| `0x13` | position | 3 x `f64` |
| `0x14` | direction | 3 x `f64` |
| `0x15` | enum | word size |

An unknown tag raises rather than being skipped. A tokenizer that guesses its way past
unfamiliar bytes produces a plausible-looking but wrong model, which is worse than a
failure.

## Records

A record is a flat token sequence terminated by `0x11`, and its leading tokens are its
class chain written **most-derived first**:

```
[    4] ENTITY_TYPE='face'  POINTER=13  INT=-1  POINTER=-1 …
[   13] ENTITY_TYPE_EX='ATTRIB_CUSTOM'  ENTITY_TYPE='attrib' …
[   16] ENTITY_TYPE_EX='plane'  ENTITY_TYPE='surface'  POINTER=-1  INT=-1  POINTER=-1
        POSITION=(-1.838, 12.203, 8.575)  DIRECTION=(0, -0, -1)  DIRECTION=(0.5, 0.866, 0)
```

### Pointers and the sections that break naive indexing

`POINTER=13` means "the fourteenth **addressable entity** in this file", and `-1` is null.
That is not the same as the fourteenth *record*, for two reasons — and getting either
wrong yields pointers that still resolve, just to the wrong things.

**Section markers are prefixes, not records.** `Begin of ASM History Data` and
`End of ASM History Section` are five type tokens glued onto the front of a real entity,
with no record terminator between them:

```
[27768] End of ASM History Section  face  POINTER=27725  INT=-1  POINTER=-1 …
```

That record *is* a face. Dropping it loses an entity and shifts every index after it.
Only `End of ASM data` stands alone, and a short body can pack a section marker and the
terminator into the same record.

**The rollback-history block is not addressable.** A `.smbh` embeds its history section
mid-file — a `history_stream` followed by `delta_state` records (in one body, records
25006–25136). Those occupy the stream but not the pointer space, and their own pointers
live in a separate index space, so they must not be walked as topology. Brute-forcing a
constant pointer offset on that body gives **0 before the block and 131 after — exactly
the block size**.

`AsmModel.entities` therefore holds the addressable entities in pointer order and
`AsmModel.history` holds the rest. `AsmModel.terminated` records whether the walk reached
`End of ASM data`; a body that parses without error but stops short was only partly
understood.

## Topology and geometry

The ACIS hierarchy, unchanged:

```
body → lump → shell → face → loop → coedge → edge → vertex → point
```

Field layouts, determined by resolving every pointer slot across every sample body and
counting what it lands on. Shapes are **uniform across ASM 231 (32-bit) and ASM 232
(64-bit)** — one shape per class, with a rare extra trailing bool on `face`. `?` marks a
slot that is null in every observed file.

| Class | Pointer slots and inline fields |
|---|---|
| `body` | attrib, ?, lump, wire, transform |
| `lump` | attrib, ?, next, shell, body |
| `shell` | attrib, ?, next, subshell, face, wire, lump |
| `face` | attrib, ?, next, loop, shell, subshell, **surface**, `sense`, `sides` |
| `loop` | attrib, ?, next, coedge, face |
| `coedge` | attrib, ?, next, prev, partner, **edge**, `sense`, loop, int, pcurve |
| `edge` | attrib, ?, **start vertex**, `f64 t0`, **end vertex**, `f64 t1`, coedge, **curve**, `sense`, str |
| `vertex` | attrib, ?, edge, int, point |
| `point` | attrib, ?, `position` |

**Edges carry their parameter range on the curve** — `t0` and `t1` are inline doubles
between the vertex pointers. Discretising an edge needs nothing beyond its curve and
those two numbers.

Two slots are legitimately null. `vertex → edge` is a convenience back-reference, not a
structural link: one sample vertex leaves it null while four edges reference the vertex.
And a **degenerate edge** has no curve at all; there are 169 such across the samples.

**Closed is not the same as degenerate.** An edge whose start and end vertex are the *same
entity* is usually a **closed** edge — a full circle, such as a cylinder's rim, whose
parameter range spans a whole period. Only when it *also* lacks a curve is it a genuine
singularity: a cone apex or a sphere pole. Treating every same-vertex edge as degenerate
throws away every circular rim in the model.

**The stored parameter range is a hint; the vertices are authoritative.** Some edges carry
a sentinel range — one sample edge one centimetre long stores `(-100, +100)` — so a
discretiser must invert the curve at the vertices rather than trust `t0`/`t1`. Across the
samples 504 of 95,668 endpoints (0.53 %) sit on such a range.

A body file can hold **several `body` records describing the same solid** — one per saved
state — all sharing a single lump and shell chain. In one sample three `body` records
reach an identical set of 423 faces. Traversal de-duplicates so a face is visited once.

**A face's loop chain can run past the end of the face.** A body saved with rollback
history can leave a loop whose `next` points at a loop bounding a different face — 9 of
the `.f3z` sample's 19,658, none at all in the three plain designs. A planar face at
*x* = -0.3 acquired a second outline 2.9 cm away at *x* = 2.6 and triangulated into
29.1 cm² where it encloses 0.8.

**The loop's own `face` pointer does not settle it.** Two faces in a rolled-back design
can reach one loop record, and the one it names is not always the one whose surface its
points lie on: one loop names face 48496 while sitting 3.4e-10 cm from face 43722's
plane, which is the face that reached it. Geometry settles it instead, and cleanly —
over 25,803 loops the 99.9th percentile distance to the face's own surface is 1.2e-05 cm
and only three exceed a thousandth.

**Tolerant topology** appears as `tedge` (578), `tvertex` (913) and `tcoedge` (4276).
Each derives from its base class with the same pointer layout plus trailing tolerance
fields, so resolving by *base* class handles them without special cases. A `tcoedge`
almost always carries a pcurve.

Geometry hangs off it as `surface` and `curve` subclasses:

| Base | Analytic | Spline |
|---|---|---|
| `surface` | `plane`, `cone`, `sphere`, `torus` | `spline` |
| `curve` | `straight`, `ellipse` | `intcurve`, `pcurve` |

Analytic forms are directly readable — a `plane` is a point plus two directions, a
`torus` is a centre, an axis, a major and a minor radius. Across the sample designs,
**13 of 22 bodies in one assembly are fully analytic**, meaning they can be tessellated
exactly with no spline kernel at all.

Spline geometry is wrapped in `0x0F` … `0x10` brackets holding nested subtypes
(`int_int_cur`, `exp_par_cur`, `nubs`, `ref`). The brackets nest and always balance, so a
record's extent is knowable without understanding the geometry inside it.

## Splines

ASM writes spline geometry **procedurally** — `int_int_cur`, `exp_par_cur`,
`srf_srf_v_bl_spl_sur`, `cyl_spl_sur`, `off_spl_sur`, `rb_blend_spl_sur`, `helix_spl_line`
— and stores an **approximating B-spline** inside the same block, which is what the
kernel itself draws. Evaluating that approximation renders a blend without
reimplementing the blend, and each block records its own fit tolerance so the error is
known rather than assumed.

### `nubs` and `nurbs`

```
nubs                            nurbs adds a weight after each control point
  int    degree                 (a surface writes two: u then v)
  …      form and closure       one enum for a curve, four for a surface,
                                sometimes preceded by a name token like `both`
  int    distinct knot count    (two for a surface: u then v)
  n x (double knot, int multiplicity)
  control points, dimension x count doubles
  double fit tolerance
```

Two things make this readable without knowing which block one is inside:

**Curves and surfaces are told apart by shape.** A curve writes one degree followed by a
form enum; a surface writes two degrees. Checking whether the second and third tokens are
both integers settles it.

**The control count follows from the knots**, and holds across every sample with no
exceptions:

```
n_control = Σ(stored multiplicities) + 2 − degree − 1
```

The `+ 2` is there because ASM stores the clamped knot vector one multiplicity short at
each end. Adding one back at both ends is what makes
`len(knots) == n_control + degree + 1` come out right. Control points are **3D for
model-space curves and 2D for parameter-space curves** (`par_int_cur`, `pcurve`).

### Interning: the `ref` table

A definition that repeats is written once and referred to afterwards as
`{ ref <n> }` — 18,236 references across the sample designs. The index counts **every
bracketed block, at any nesting depth, in file order, excluding the `ref` blocks
themselves**, 0-based.

That rule was not obvious: several plausible numberings put every index in range, so
"nothing overflows" proves nothing. What settles it is asking what a reference has to
*mean* — a `spline` **surface** record's reference must name a surface. Counting inline
field names as entries gets 4 of 21; counting bracketed blocks gets 21 of 21, and across
all four designs every one of the 1,569 surface references lands on a surface.

## Attributes

`ATTRIB_CUSTOM` / `attrib` records carry Fusion's own tags:
`generic_tag_attrib_def` (integer tags that survive rebuilds and are how the timeline
addresses individual faces and edges) and `Timestamp_attrib_def` (rollback bookkeeping).
Decoding the tag attributes is what will let a feature reference like "fillet these two
edges" be resolved to actual topology.
