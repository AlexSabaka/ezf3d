# What is still opaque

An honest list. Each entry says what is not understood and what decoding it would unlock.

## Bulk stream record bodies

**Status:** type names recovered, record bodies not.

The design segment's payload is a typed object graph whose meta-type names read plainly
(`DcExtrudeFeatureMetaType`, `SketchesRoot`, parameter ids like `d73`, expressions like
`80mm`), but the record *bodies* need a decoder per subsystem schema revision — the
observed versions span `269` through `489` across four documents.

**Unlocks:** parameters with names and expressions, sketch entities and constraints, the
ordered feature timeline, the component tree, joints. Everything Phase 3 and Phase 4 need.

## Meta stream records

**Status:** header decoded, per-module records not.

After the header, a meta stream holds records keyed by module GUID that name the object
ids present in the bulk stream, roughly
`str8 guid, str8 guid, u32, str8 kind, u32 n, n x u64 ids`. The exact shape varies by
module.

**Unlocks:** an index into the bulk stream, so records can be found by id rather than
scanned for.

## Resolved since first writing

The ASM topology field layouts, the pointer index space (main-section entities, history
block excluded, markers as prefixes), tolerant topology, closed versus degenerate edges,
sentinel parameter ranges, and the analytic surface and curve fields — including
elliptical cones — are all decoded; see [asm.md](asm.md#topology-and-geometry). They are
enforced by tests rather than only described here: a pointer contract in
`tests/test_asm.py`, and in `tests/test_geometry.py` the check that every vertex lies on
its edge's curve to within the kernel's own tolerance.

## Tessellation gaps (ezf3d's, not the format's)

Two limitations in ezf3d's own triangulation, recorded here so they are not mistaken for
format problems:

**Faces with several holes can leave non-manifold edges.** Bridging a hole into its outer
loop cuts the polygon so the ear clipper can see one outline, and on a face with five
holes the cuts occasionally overlap. 108 of 144 closed, fully meshed solids come out
watertight; the rest carry a handful of edges used more than twice. `ezf3d mesh` reports
the rate rather than assuming it.

**Notched regions on a curved surface fall back to a fan.** A face whose parameter-space
outline is monotone in neither direction cannot be walked as a strip, and a fan cuts the
chord. 12 of 6,109 cone faces across the samples; they are counted in
`faces_over_tolerance`.

## Header words of unclear role

- Document manifest: `u32 20` and a third word (`53` / `18`), plus a magic
  (`0x2A400040` / `0x2A340040`) whose halves co-vary with the third word.
- ASM header: the third and fourth prelude words (`(10, 2)`, `(2, 3)`, `(136, 2)`).
  They correlate with neither record count nor body count.
- Bulk stream `flags`: `0` everywhere except browser streams, where it is `2`.

**Unlocks:** nothing urgent. Recorded so a future contradiction is noticed rather than
silently absorbed.

## Spline geometry

**Status:** located and bounded, not evaluated.

`intcurve`, `pcurve` and `spline` records wrap `int_int_cur` / `exp_par_cur` / `nubs`
subtypes in balanced `0x0F` … `0x10` brackets. The brackets are enough to walk past them;
the knot vectors and control points inside are not yet read.

**Unlocks:** exact tessellation of every body rather than only the fully analytic ones.
This is the hardest single item on the roadmap.

## ASM tag attributes

**Status:** located, not decoded.

`generic_tag_attrib_def` attributes carry integer tags that persist across rebuilds. The
timeline almost certainly addresses individual faces and edges through them.

**Unlocks:** resolving a feature reference ("fillet these edges") to actual topology —
required for faithful transpilation in Phase 4.

## Protein materials

**Status:** container identified, contents not read.

`*.protein` files are nested ZIPs holding `<GUID>/AssetData/*.bin`.

**Unlocks:** real material names, colours and physical properties, for rendering and for
mass properties in Phase 5. The design stream already names the material in plain text
(`i4 Custom Materials|PLA`, `PrismMaterial-018`), so a coarse answer is available without
decoding the blobs.

## OGS graphics cache

See [graphics-cache.md](graphics-cache.md).

## Design configuration tables

`DesignConfigurationTable.BlobParts/*.dsgcfg` and `*.dsgcfgrule` are two bytes (`{}`) in
every sample — empty configurations. A design that actually uses configurations would be
needed to learn anything.
