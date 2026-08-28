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

Six record types are now decoded field by field: the component (its name and the id
range it owns); the **parameter** — name, role, unit, expression and value, 1,193 of them
across the four samples, each checked four ways; the **feature**'s tail, enough for its
name, its inputs and its place in the timeline, plus an extrude's operation and
direction; the material **assignment**; the sketch **point**, whose coordinates and
owning sketch both read; and the sketch **curve** — its kind, the points it names, its
radius and its span. See [neutron-streams.md](neutron-streams.md#parameters). The
rest are still bytes.

**Unlocks:** sketch constraints, the remaining feature payloads, joints. Everything Phase
4 needs beyond the profile, which now reads.

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

### Parameters, with their expressions and units

Decoded; see [neutron-streams.md](neutron-streams.md#parameters). What made it tractable
is the object index: a parameter is one 140–260 byte record with a known extent, so its
fields could be read directly instead of parsed to.

What is *not* decoded is the 31-byte preamble before the expression — only the `u32` at
+16, which repeats the number in the auto-generated name — nor the reference at +20,
which points at a neighbouring object. Nine or ten bytes between the expression and the
role are zero in every record seen and are skipped rather than named.

ezf3d does not evaluate expressions. `d155 = d154` and `1.5 in / 2` are reported as
written; the stored value is what it reports as the value.

### The timeline order

Decoded; see [neutron-streams.md](neutron-streams.md#the-timeline). The list is identified
by the fact that every entry resolves to the object *preceding* it in the index, and
checked against the registry two ways.

What is **not** established is that this list is everything Fusion shows. It holds all of a
single-component design's features, but Robotic_Bhujha's 35 joint origins, component
creations and placements sit outside it, and no second ordered list holds them — the ones
that exist are reached by direct reference, with no order to read. `ezf3d timeline` counts
what is outside rather than implying the list is the whole timeline.

There is also no internal ground truth for the *order* itself: a list is a list, and
nothing in the file says it is the timeline. The evidence is circumstantial and good — the
sequence reads like one, and it disagrees with creation order exactly where a designer
would have dragged the marker back — but confirming it means opening the file in Fusion.

The `u32` before a feature's name is unread: it is `0xFFFFFFFF` on sketches, work planes
and paste/remove-body, and an ascending number elsewhere, but it is not the timeline index
and not a merge key. The `<u32 k> <f64> <u32 k>` triples in feature and item records look
like keyed attributes — the values are small fractions that behave like recompute times —
and are not read either.

### What a feature *does*, as opposed to by how much

**Status: solved for extrudes, open for everything else.** An extrude's operation and
direction are decoded and checked against a Fusion readout — see
[neutron-streams.md](neutron-streams.md#what-an-extrude-does). What follows is what is
still open, and the record of how the first attempt went wrong.

The numbers are read (see
[neutron-streams.md](neutron-streams.md#what-a-feature-drives)); the choices are not. For
an extrude that means the operation — join, cut, intersect, new body — the extent type, the
direction, and which sketch supplies the profile.

None of it is tagged. A census of `str8` keys inside every timeline feature and its item
turns up only `edge_recipe_data`, `body_recipe_data`, `face_recipe_data`, `EntityGenesis`
and `crv_primary_id`, which are entity references, plus `DcFeatureOperationIdFlag` — which
appears on `Combine` only, 19 times, and whose value is a tracked-entity id (385, 386)
rather than an operation.

**Records of one kind do not align between documents**, which is the trap here. Aligning
SUCKER's eight extrudes backwards from their guid gives 39 candidate fields, and one — an
`f64` at guid − 128 — reads as exactly `+1.0`, `−1.0` and `0.0`, which looks decisively like
a direction. Run over all 214 extrudes of the four samples it is noise (`-3.1e+231`,
`9.4e-322`). It was an alignment coincidence inside one document, and it is recorded here
because it is what a plausible-and-wrong finding looks like. Record size does not classify
either: SUCKER's extrudes come in four sizes and Robotic_Bhujha's in twenty-five, tracking
how much entity-reference data each carries.

**How the extrude case was cracked**, since the same method is what the remaining kinds
need: a Fusion readout of one design's eight extrudes named three groups; aligning the
records inside that one document turned up exactly two byte columns constant within each
group; and reading the bytes around them showed a `0x01` flag and three `u32` rather than a
lone byte. The first alignment — a fixed distance back from the guid — was *wrong*, and
looked right for seven of eight records; the record that broke it was the one with a
different number of inputs. Anchoring on the revision string instead fixed it and took the
reader from 7/8 to 214/214.

**What is still open:** the same block has not been found on any other kind, and Fusion
gives fillets, chamfers, revolves and combines operations too.

**Corrected.** This entry used to end by calling the **profile** unreachable, on the
grounds that an extrude's input list never names its sketch. The premise is right — 0 of
88 — and the conclusion was wrong. The reference runs the other way: each point and curve
record names the sketch that owns it, and the sketch geometry reads. See
[neutron-streams.md](neutron-streams.md#sketches). The error was looking for the link only
where the feature record would have put it, and concluding from its absence there that it
was absent everywhere.

**What it blocks:** Phase 4 still, but less of it. Of the five things needed to regenerate
an extrude — profile, distance, taper, direction, operation — four were readable and the
profile now has a *shape*. What it does not have is a **place**; see below.

### Resolved: sketch curve types

Decoded; see [neutron-streams.md](neutron-streams.md#what-kind-of-curve-it-is). This
entry used to say that record size separated circle from line from arc *inside* one
document and could not be trusted across documents — which was right, and is why the
count of point references was looked for instead. It types **1,334 of 1,334** curves, and
the arcs' own geometry confirms it: the stored radius is the distance from the centre
they name to the endpoint they name, and the stored span the angle those endpoints
subtend, for all 282.

What remains unread inside a curve record is everything before the radius: roughly 80
bytes whose `f64` layout differs by kind. Nothing needs them yet.

## The sketch plane

**Status: not in the design stream; recoverable from the geometry, but not uniquely.**

Sketch coordinates are two-dimensional in the sketch's own frame: `z` is zero for 1,876 of
1,912 points and never exceeds 1.8e-15 in the rest. Placing a profile in 3D needs that
frame, and the design stream does not appear to carry it. No orthonormal 3x3 basis occurs
anywhere in the id range a sketch owns, and a sketch's `inputs` point at 30-byte tag
objects rather than at a plane or a face.

**The B-Rep supplies it, and the recovery proves itself.** An extrude sweeps a profile, so
the profile's loop turns up as the boundary of a planar face. Fitting
`world = origin + x·u + y·v` as a *free* affine map — nine unconstrained numbers, nothing
asking the axes to be perpendicular or unit length — yields orthonormal axes anyway, worst
departure 6.9e-13 and worst residual 7.0e-10 cm. See
[neutron-streams.md](neutron-streams.md#where-a-sketch-sits). A parameter from the design
stream corroborates it against ASM vertex positions: SUCKER's slot has two candidate faces
0.02 cm apart, which is the −0.2 mm its extrude sweeps.

**What is still open is which one.** A design repeats its own shapes, so a profile fits at
every instance of a patterned feature. SUCKER's slot lands in 8 places and that design's
`R-Pattern1-vCount` is 8 — the ambiguity is the design's own multiplicity, not noise, and
nothing in the geometry marks the seed. Only 49 of 130 sketches match at all; the rest have
been filleted, cut or shelled since.

**A second route does better.** An ASM `sketch_attrib_def` names the sketch curve a B-Rep
edge came from, so the correspondence is read rather than matched, and grouping those edges
by shared world vertices separates a patterned copy from its seed — something shape matching
cannot do, since congruent is congruent. 25 of its 52 placements land in exactly one place
against 2 of shape matching's 13, and the two routes together reach 64 of 130 sketches. See
[ASM tag attributes](#asm-tag-attributes).

**What would settle the rest:** which body a feature produces, or which face it consumes.
`generic_tag_attrib_def` is the obvious place — 5,629 records against the sketch
attribute's 1,163, most of them on faces.

**Also unread: profiles that close against geometry outside the sketch.** SUCKER's sketch
#26 has 24 curves and *no* closed loop: 14 of its endpoints carry one curve each, and none
of them coincides with any other point the sketch's curves use. An extrude consumes it
nonetheless, so its profile must close against edges projected from the body — which are
not in the sketch's own records. 480 of the samples' 1,334 curves sit in no loop, and this
is why a good share of them do. A transpiler that assumed a sketch is self-contained would
be wrong about those.

**Also unread: which profile an extrude picks.** A sketch offers several loops — SUCKER's
sketch #31 offers two — and nothing yet says which one a given extrude sweeps.

**What it blocks:** faithful transpilation. Four of an extrude's five inputs read; the
profile has a shape, a set of candidate places, and no way to choose among them.

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

**Status: the sketch attribute and its scope are both decoded.**

`ATTRIB_CUSTOM` records name a definition and carry a payload. Across SUCKER's twelve
bodies there are four: `generic_tag_attrib_def` (5,629), `sketch_attrib_def` (1,163),
`Timestamp_attrib_def` (68) and `FPM_tracked_attrib_def` (63). Slots 2, 4 and 5 of a record
chain to sibling attributes and slot 6 is the entity it hangs off.

**`sketch_attrib_def` is the link the design stream does not carry.** It hangs off a
**coedge** and holds a string of six integers — `"1563 1589 1 0 1 1"` — whose first two are
the `(crv_primary_id, crv_secondary_id)` that the sketch curve record itself holds. So a
B-Rep edge says which sketch curve drew it, by a key written into two different streams.
See [neutron-streams.md](neutron-streams.md#sketches). The other four columns are two small
counters, a sense flag, and a column that is always zero.

**The key is scoped to a component.** Robotic_Bhujha reuses **159 of its 331** keys across
the document — one belongs to five circles at once — but **none within any of its ten
components**. SUCKER and the fan reuse none at all. Since a body belongs to a component and
a component owns its sketches, the body already says which table to read. Only Focuser Mk1
still collides after scoping, on 44 keys at multiplicity two, and it is the one sample that
is an assembly of XREF'd documents — so its numbering spans files the component boundary
does not. It is also scoped no wider than a sketch: no two curves of one sketch share a key
anywhere in the samples.

A key is therefore resolved against the sketches of the body's own component first, and
against the whole document only where exactly one curve claims it.

Getting the scope wrong is not a hypothetical failure. A B-Rep edge that closes on itself
can only have come from a full circle, and read with no scope at all **209 closed edges
named lines**. Scoped to the body's component, **794 of 796 name circles** — the curve kind
coming from the design stream and the closure from vertex identity in the ASM stream, two
parsers with nothing in common. The two exceptions are both in the XREF'd assembly.

The link reaches **8,333 edges naming 1,079 of 1,334 curves across 125 of 130 sketches**,
where refusing every key shared anywhere in the document reached 2,714 and 73.

**What it unlocked:** placing a sketch from the edges that name its curves, rather than by
matching shapes. Grouping those edges by shared world vertices separates a patterned copy
from its seed, which shape matching cannot do — congruent is congruent. 25 of its 52
placements land in exactly one place, against 2 of shape matching's 13. See
[the sketch plane](#the-sketch-plane) for what is still ambiguous.

`generic_tag_attrib_def` is the larger population and is still unread. It is what the
timeline most likely addresses faces and edges through.

### Material assignments

Decoded; see [neutron-streams.md](neutron-streams.md#materials). Which component and which
body is assigned which protein asset, checked both ways against the package that declares
it.

The **readable name** is not read. `PrismMaterial-018` is an appearance id; the material's
display name ("Steel", "Steel, mill finish") sits in the package's `InstanceProperties.bin`
and `DefinitionIteratorProperties.bin`, whose records are keyed by the schema documents
shipped beside them. Reading them properly means decoding the Protein property format, and
the strings are there in order but only positionally — which is a guess, not a reading. A
user-library material is the exception: Fusion writes `i4 Custom Materials|PLA` into the
design stream itself.

The second and third slots of an assignment are inferred from how they trade places: an
Autodesk-library material fills the library guid and leaves the name empty, a user-library
one does the reverse. Nothing in the file labels either.

## Protein materials

**Status:** container read, property streams not.

`*.protein` files are nested ZIPs holding `<GUID>/AssetData/*.bin` beside the schema
documents that describe them. `AssetTableOfContents.bin` is decoded — a flat run of `str8`
values pairing each asset id with a category — and that is what lets ezf3d say a design's
assignment names a `physicalmaterial`. `InstanceProperties.bin` and
`DefinitionIteratorProperties.bin` are not: their values are keyed by the schemas, and the
readable strings inside them ("Steel", "Steel, mill finish", "Metal/Steel") can be listed
but not attributed without decoding the property format.

**Unlocks:** real material names, colours and physical properties, for rendering and for
mass properties in Phase 5. The design stream already names a user-library material in
plain text (`i4 Custom Materials|PLA`) and always names the appearance
(`PrismMaterial-018`), so a coarse answer is available without decoding the blobs.

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
