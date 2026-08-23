# What is still opaque

An honest list. Each entry says what is not understood and what decoding it would unlock.

## Bulk stream record bodies

**Status:** every record located and delimited; the fields inside one not decoded.

The meta stream's index gives each object an id, a byte offset and — through the next
entry — an extent, so `Segment.object_bytes(id)` already hands back exactly one record:
385 of them in the wheel, 14,843 in Robotic_Bhujha. What is not decoded is the *inside*,
which needs a schema per type per subsystem revision — the observed revisions span `269`
through `489` across four documents.

The difference matters. A decoder can now be written for one type at a time and tested
against exactly the bytes of one record, rather than having to parse a 2.7 MB stream
sequentially to reach anything.

**Unlocks:** parameters with names and expressions, sketch entities and constraints, the
ordered feature timeline, the component tree, joints. Everything Phase 3 and Phase 4 need.

## Resolved since first writing

### The meta stream, including its object index

Fully decoded; see [neutron-streams.md](neutron-streams.md#segments). It is not merely a
list of ids: it maps **object id to byte offset in the bulk stream**, ascending in both,
so consecutive entries delimit each object. All fourteen segments of the samples walk to
exactly the record count their header declares, and every byte between the index and the
footer is accounted for except a 728-byte section in two `.f3z` members, which is
reported as a count.

That changes what remains below. Decoding a bulk record no longer means finding it.

The ASM topology field layouts, the pointer index space (main-section entities, history
block excluded, markers as prefixes), tolerant topology, closed versus degenerate edges,
sentinel parameter ranges, and the analytic surface and curve fields — including
elliptical cones — are all decoded; see [asm.md](asm.md#topology-and-geometry). They are
enforced by tests rather than only described here: a pointer contract in
`tests/test_asm.py`, and in `tests/test_geometry.py` the check that every vertex lies on
its edge's curve to within the kernel's own tolerance.

## Spline surface identification

**Status: unresolved, and gated off because of it.**

Reading a `nubs`/`nurbs` surface works — the localised tensor-product evaluation is
bit-identical to the full sum. What is not established is **which** approximating spline
belongs to a given face. A procedural block nests several, and the one ezf3d picks for a
`spline` face sits, even sampled at 80x80, a median 2.4e-02 cm from the face's own
vertices — far outside any fit tolerance the file states. Tessellating it produced worse
geometry than leaving the face out, so `TESSELLATE_SPLINE_SURFACES` is `False` and those
faces are reported as unsupported.

Spline **curves** are unaffected and are used: an edge knows two points its curve must
contain, so an approximation is validated before being trusted, and 94 % of spline edges
pass with a worst miss of 8.3e-05 cm.

Fusion's own cached mesh has since confirmed this independently, and sharpened it: over
matched faces, cached vertices sit within 1e-07 cm of every analytic surface ezf3d reads
and 2.3e-02 cm from the spline surface it picks. The surfaces are read correctly; the
wrong one is being chosen.

**Unlocks:** the last ~4 % of faces on the sample designs, and complete meshes for
spline-heavy parts.

## Tessellation gaps (ezf3d's, not the format's)

Recorded here so they are not mistaken for format problems.

**Notched regions on a curved surface fall back to a fan.** A face whose parameter-space
outline is monotone in neither direction cannot be walked as a strip, and a fan cuts the
chord. Those faces are reported rather than meshed once they stray four times past the
tolerance — 85 of Robotic_Bhujha's 2,405, 90 of SUCKER's 3,498.

**Periodic faces ezf3d cannot cut.** A face wrapping in *u* with more than the two rings
a strip needs is reported: 30 in the wheel, 4 in SUCKER.

**A few faces with holes still cannot be bridged.** Splicing a hole into its outer loop
has to cut to a vertex the hole can actually see, and two constructions are tried — a ray
cast to the right, then the nearest vertex to the right. Neither dominates the other, and
on 27 faces across the four samples neither produces a triangulation of the right area.
Those faces are reported, not emitted: 2 in the wheel, 4 in SUCKER, 21 in the `.f3z`,
none in Robotic_Bhujha.

### Resolved: holes that were being filled in

Splicing a hole into its outer loop repeats two vertices, and an ear-clipping containment
test that went by index found those duplicates on every candidate ear, rejected all of
them, and fanned over the whole outline. Faces came out with their holes filled and
closed solids came out non-manifold: 80 of Robotic_Bhujha's 116 closed solids watertight,
and the wheel reporting **2,003 cm²** of surface where its meshable faces come to **718**.

It was invisible to every check the suite had. Filled triangles lie on the plane like any
other, so the deviation check passed; they close the surface, so watertightness passed;
there were the same number of them, so a count passed. **Fusion's own cached mesh is what
showed it** — see [graphics-cache.md](graphics-cache.md#it-found-a-bug).

Robotic_Bhujha is now 116 of 116 and the `.f3z` 296 of 296, and `tessellate_face` rejects
any multi-loop face whose triangles do not come to the *unsigned* area its loops enclose.
Unsigned matters: a fan that covers the hole emits it with the opposite winding, so the
signed total still comes out right and only the absolute total gives it away.

### Resolved: loops that bound another face

A body saved with rollback history can leave a loop whose `next` points at a loop
bounding a **different face** — 9 of the `.f3z` sample's 19,658, none at all in the three
plain designs. Following one hands a face an outline that is not on its surface: a plane
at *x* = -0.3 was given a second loop 2.9 cm away at *x* = 2.6.

The loop's own `face` pointer looked like the answer and is not — two faces can reach one
loop record and the pointer names only one of them, sometimes the other one. Geometry
decides instead: a loop whose points do not lie on the face's surface is not that face's
loop. The separation is clean, over 25,803 loops the 99.9th percentile distance is
1.2e-05 cm and three exceed a thousandth, so the two faces this rejects are rejected on
centimetres rather than on a judgement call.

## Header words of unclear role

- Document manifest: `u32 20` and a third word (`53` / `18`), plus a magic
  (`0x2A400040` / `0x2A340040`) whose halves co-vary with the third word.
- ASM header: the third and fourth prelude words (`(10, 2)`, `(2, 3)`, `(136, 2)`).
  They correlate with neither record count nor body count.
- Bulk stream `flags`: `0` everywhere except browser streams, where it is `2`.

**Unlocks:** nothing urgent. Recorded so a future contradiction is noticed rather than
silently absorbed.

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

Decoded; see [graphics-cache.md](graphics-cache.md). What is still unread there is the
attribute side — colour, visibility and transforms — and the sketch display geometry.

One thing it does **not** offer, recorded so it is not hoped for twice: assembly
placement. The `.f3z` sample's cache draws ten bodies at once and every one of its 5,292
edge polylines ends on a `point` record of one of them — with no transform applied. Those
ten bodies already share a frame in their own ASM, so the cache adds nothing about where
they sit. Whether that holds for an assembly whose parts *are* modelled about their own
origins is untested; the sample that behaves that way, Robotic_Bhujha, has no cache.

## Design configuration tables

`DesignConfigurationTable.BlobParts/*.dsgcfg` and `*.dsgcfgrule` are two bytes (`{}`) in
every sample — empty configurations. A design that actually uses configurations would be
needed to learn anything.
