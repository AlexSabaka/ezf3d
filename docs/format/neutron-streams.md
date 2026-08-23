# Neutron streams

Every `.dat` file in a Fusion document uses one serialization. Autodesk calls the
subsystem *Neutron* (the ASM bodies identify their producer as `Autodesk Neutron`), so
that is the name used here.

## Primitives

| Encoding | Layout |
|---|---|
| `str8` | `uint32` **byte** count, then that many bytes of ASCII/UTF-8 |
| `wstr` | `uint32` **character** count, then `2 x count` bytes of UTF-16LE |
| scalars | little-endian `u8` / `u16` / `u32` / `u64` / `i32` / `i64` / `f32` / `f64` |
| GUID | a `wstr` of exactly 36 characters, or a `str8` of 36 in some records |

There are **no type tags on the wire**. A reader must already know the schema of the
record it is standing on — which is what makes this format resistant to casual
inspection, and why [`scan_strings`](../../src/ezf3d/streams/primitives.py) exists: when
a schema is unknown, scanning for self-consistent strings is how it gets discovered.

## Document manifest — `/Manifest.dat`

```
str8   format_version        "3-2-0-0"
str8   doc_type_key          "FusionDocType"
wstr   extension             ".f3d"
wstr   doc_type              "Fusion Document"
wstr   doc_description       "A Fusion Document"
wstr   document_guid
wstr   lineage_guid
       <version block>       see below
u32    n; n x (str8 name, u32 version)     schema version table
u32    n; n x wstr                          related GUIDs
u8     has_items; if set: u32 n; n x item   see below
wstr   content_guid
u32    n; n x wstr                          asset folder names
u32    0
u8     1
wstr   origin
```

The **schema version table** is the key to reading anything else — it records which
revision of each subsystem wrote the document:

```
Application: 1     CAM: 4      ParaMesh: 8         SimCommon: 30005
SimFEACSObjects: 2 SimFluidDynamics: 2  SimStructuralAttributes: 10002
```

`origin` is `NA_OFFLINESAVE` or `NA_EXPORT` for a document saved from the desktop app,
and the design's own name for one produced by the cloud translator.

An **item** (only present when `has_items` is set) is
`wstr x4, u32 x4, u32 n, n x (wstr key, wstr value)`.

## Version blocks

The block between the identity GUIDs and the schema table changed size between
revisions:

| Revision | Layout |
|---|---|
| current | `u32 1234` (sentinel), `u32 20`, `u32 ?`, `u32 magic` |
| older | `u32 build_stamp`, `u32 14` |

Rather than switch on a version number whose full range is unknown, ezf3d tries each
candidate width and accepts the one after which a **plausible schema table** decodes —
a bounded count whose keys are all ASCII identifiers. The same trick handles the meta
stream header. This is deliberate: a document from a Fusion release nobody has tested
against is more likely to keep working than to fail loudly on an unrecognised constant.

## Asset manifest — `<Asset>[State]/Manifest.dat`

```
wstr   asset_name            "FusionAssetName"
wstr   asset_guid
wstr   revision_guid
str8   asset_type_key        "FusionAssetType" / "AnimationAssetType"
u32    20
u32    n; n x (str8, u32)    asset schema table
str8   asset_type            "Neutron3DAssetType"
u32 0; u8 0
u32    n; n x (str8 key, wstr value)        properties, e.g. physicalChangeGuid
u32    n; n x reference                     parent assets, 3 GUIDs each
u32    change_counter
u32    n; n x (u32 slot, str8 prefix, str8 segment_type)
```

The last table is the **segment table**. The folder on disk is `prefix` plus an instance
number, so `("ACT", "FusionACTSegmentType")` describes the folder `ACT1`. `slot` is an
identifier, not a sequential index — one observed document uses slots 0, 1 and 4.

A reference record is `u32 kind, wstr, wstr, u32, u32, wstr`. It appears when an asset
derives from another: an `Animation` asset names the design asset it animates.

## Segments

A segment is one subsystem's slice of the document, stored as two files.

**`MetaStream.dat`** — the index, and it earns the name:

```
str8   prefix                folder prefix, matching the manifest declaration
u32    slot                  matching the manifest declaration
wstr   guid                  all-zero for a segment that has never branched
       <version block>
str8   declared_type         "FusionDesignSegmentType" (plain "Design" in old files)
str8   owner                 "Fusion" / "Animation"; empty in old files
u32    ?, u32 ?, u32 record_count
record_count x {
  str8 identity              this record's own GUID, unique in the segment
  str8 group                 a GUID several records share
  u32  kind                  0-4; meaning not established
  str8 owner                 Fusion / Geometry / EntityTracking / Component / ...
  u32  n, n x u64 ids        object ids this record refers to
}
u32    n_roots, n_roots x u64          the segment's root objects
{ u32 n, n x (u64 object_id, u64 bulk_offset) } ...    the object index
u64    next_id, u32 0        one past the highest id ever issued
u32    2, { str8 subsystem, u32 revision } x 2         optional footer
```

The record count is exact: all fourteen segments across the samples walk to
precisely the number the header declares.

### The object index

The heart of it. `object_id → byte offset in the bulk stream`, and **the offsets
ascend with the ids**, so consecutive entries delimit each object:

| sample | indexed objects | median object | largest |
|---|---|---|---|
| Mk1 Focuser, Wheel 2 | 385 | 99 B | 11.6 KB |
| SUCKER | 3,444 | 103 B | 31.6 KB |
| Robotic_Bhujha | 14,843 | 96 B | 150 KB |
| Focuser Mk1 (`.f3z`) | 26,950 | — | — |

**The bulk stream is therefore randomly addressable and its records have known
extents**, without any decoder for what is inside one. `Segment.object_bytes(id)`
hands back exactly one.

Corroboration that the offsets are real boundaries comes from outside the index:
the type-name strings are found by scanning the bulk stream for length-prefixed
strings, which knows nothing about it. Every one of them — 16, 1,150 and 4,307
across the three plain designs — falls inside exactly one indexed object, and
none straddles a boundary.

`next_id` is a high-water mark rather than a count: the wheel's design segment
indexes 385 objects, its largest id is 452, and `next_id` is 453. SUCKER's is
13,812 against 3,444 live objects, so most ids have been retired.

The two GUIDs are told apart by how they repeat. `identity` is unique across a
segment's records in all fourteen — 299 distinct across Robotic_Bhujha's 299.
`group` repeats, 45 distinct over the wheel's 167 with one used 22 times. Read
the other way round the list looks like a chain, and for the first five records
it convincingly is; over the whole list only 44 of 167 link, so that is a
coincidence of the opening records.

**`BulkStream.dat`** — the payload:

```
str8   version               a numeric string: "299", "397", "489"
u64    flags                 0, except 2 in browser streams
       ... typed object graph
```

Both streams are **uncompressed**. The bulk stream is a typed object graph whose records
carry readable type names:

```
DcSketchMetaType         DcExtrudeFeatureMetaType      DcRevolveFeatureMetaType
DcLoftFeatureMetaType    DcFilletEdgeFeatureMetaType   DcChamferFeatureMetaType
DcShellFeatureMetaType   DcHoleFeatureMetaType         DcThreadFeatureMetaType
DcCircularPatternMetaType  DcRectangularPatternMetaType  DcPathPatternMetaType
DcJointOriginFeatureMetaType  DcJointAssembleFeatureMetaType  DcMotionLinkFeatureMetaType
roots: ComponentsRoot, ComponentInstancesRoot, BodiesRoot, SketchesRoot, UnitSystems
refs:  StrongRefMetaType, PassiveRefMetaType, IntrinsicMetaTypeuint64
```

### The design graph

With every object located, the graph they form reads without decoding a single
field inside one. Three things carry it.

**The roots name themselves.** An object the meta stream lists as a root opens
with its own type as a `str8`: `ComponentsRoot`, `BodiesRoot`, `SketchesRoot`,
`UnitSystems`, `ProteinAssetManager`, `rootInstance`, `AssetSettings`,
`VisualAnalyses`, `NamedTrackedEntitySet`, `WorkingModelPlaceholderRoot`,
`OGSSerializer`, and the two configuration triggers. Ordinary objects open with
an empty string, so a leading name is exactly what marks a root.

**A reference is `0x01` then a `u64` object id.** Read that way,
`ComponentsRoot` yields precisely the components — one for the wheel and for
SUCKER, eleven for Robotic_Bhujha, seven for the `.f3z` root.

That pattern is permissive: `0x01` followed by eight bytes that happen to spell
a small number is common enough that following references transitively reaches
everything from anywhere. Walking from any one of Robotic_Bhujha's components
reaches all 22 bodies. So it is trustworthy for reading a record known to be a
list, and useless for deciding ownership.

**Ids are issued in creation order, so a component owns a contiguous range.**
Everything between one component's id and the next belongs to it. Component 489
(`BASE`) owns 493 — its `.smbh` body — 509, its `.smb`, and 510, its feature
registry.

The evidence is that the ranges come out exactly right:

| sample | components | bodies named | in `Breps.BlobParts` |
|---|---|---|---|
| Mk1 Focuser, Wheel 2 | 1 | 2 | 2 |
| SUCKER | 1 | 2 | 2 |
| Robotic_Bhujha | 11 | 22 | 22 |
| Focuser Mk1 (3 documents) | 7 + 1 + 1 | 14 + 1 + 1 | 14 + 1 + 1 |

Every component owns precisely two bodies — the `.smbh` carrying rollback
history and the `.smb` without — and every blob on disk has exactly one owner.
The two counts come from different parts of the archive found by different
scans, so their agreement is a real check.

It also settles the registry question: **Robotic_Bhujha's eleven feature
registries are its eleven components**, one each. A component with no timeline
of its own has none; two of the package's members are like that.

**A component's name is the last wide string before its trailing revision.**
Fusion writes a GUID there for a component the user never named — two of
Robotic_Bhujha's eleven — and `(Unsaved)` for a design saved from an unsaved
state.

### The registries: a dictionary of kinds, with a counter each

They arrive in **registries**: one entry per kind, sorted by name, that the objects index
into. SUCKER holds one of 17 entries at offset 9558; Robotic_Bhujha holds eleven, of 2 to
14 entries each.

Two measurements say a *count of the names* describes the dictionary rather than the
design. No registry repeats a name, and a name's total across the stream is exactly the
number of registries that declare it — so Robotic_Bhujha's nine
`DcExtrudeFeatureMetaType` are nine registries that permit an extrude, not nine extrudes.

**There is a count, though, and it is beside each name rather than in it.** Every registry
entry is `str8 name` followed by a `u64`, and that number is Fusion's per-kind counter —
what its timeline labels count up with. It was being stepped over; this document used to
say a count here was meaningless, and only the count *of names* is.

Summed across a design's registries, the counter is an **ever-created** tally and so an
upper bound on the live timeline, not a census of it:

| design | issued | live in the timeline |
|---|---|---|
| Mk1 Focuser, Wheel 2 | 3 | 3 |
| SUCKER | 83 | 58 |
| Robotic_Bhujha | 269 | 225 |
| Focuser Mk1 | 615 | 400 |

### The timeline

A feature is an ordinary bulk object with a recognisable tail:

```
wstr guid              all-zero for most features
0x00 0x00 0x00
u32 n, n x reference   the inputs — sketches, faces, bodies it consumes
u32 token              0xFFFFFFFF where Fusion wrote none
wstr name              "Extrude", "Fillet", "Base Feature"; may be empty
```

A record can hold more than one guid and the tail only reads correctly after one of them,
so a **named** parse wins over an unnamed one — taking the first guid that merely parses
cost Robotic_Bhujha all eight of its `Assemble` features.

**Order is not creation order.** Every feature is followed in the object index by a small
*timeline item*, and one object lists those items in the order the timeline runs. SUCKER's
list interleaves ids 1270, 12657, 12825 — a feature created near the end of the design
sitting tenth, which is what dragging the timeline marker back and inserting there
produces. Ordering by object id would put it last.

Nothing points from an item back to its feature: the feature is simply the object *before*
it in the index. That is what identifies the list among every other reference list in the
stream — ezf3d accepts only a list whose every entry resolves that way, and takes the
longest. It resolves 3 of 3, 58 of 58, 225 of 225 and 400 of 400, and there is exactly one
such list per design. Relaxing it to "or the reference is itself feature-shaped" turned
seven body-appearance records into a seven-entry timeline in a package member that
declares no feature kind at all.

The name is the label Fusion shows, and it is not always the registry kind: `RemoveBody`
for `DeleteBody`, `CopyPasteBodies` for `PasteBodies`, `C-Pattern` for `CircularPattern`,
`Fillet` for `FilletEdgeFeature`, `Position` for `SnapshotFeature`. Most are the kind with
`Feature` dropped, or the same name with its spaces removed.

Two checks say the reading is right rather than merely self-consistent, and both come from
the registry, which is written separately from the list: **every label names a kind the
registry declares**, and **no kind appears in the timeline more often than its counter says
was ever issued**. Both hold for all four samples.

`ezf3d timeline` also counts the named features a design holds that the list does *not* —
2 in SUCKER, 35 in Robotic_Bhujha, 190 in the Focuser package. In the single-component
designs those are deleted or superseded work; in Robotic_Bhujha they are its joint
origins, component creation and placement, which this list does not carry. Said out loud,
because a timeline that quietly drops a design's joints reads as complete.

### Match whole strings, not raw bytes

The names must be found by scanning for length-prefixed strings and matched **whole**.
Matched as a pattern over raw bytes, the same names appear far more often and truncated:

| sample | raw-byte "hits" | real type-name strings |
|---|---|---|
| Mk1 Focuser, Wheel 2 | 16 | 12 |
| SUCKER | 1,150 | 32 |
| Robotic_Bhujha | 4,307 | 108 |

Every extra hit is a real string cut short. All 1,118 of SUCKER's `IntrinsicMetaType`
are really `IntrinsicMetaTypeuint64` — a scalar-type declaration, with the value type
glued to the name — and three more are `IntrinsicMetaTypeIString`, `…bool`, `…HString`.
Reporting the prefix turns a type declaration into a phantom timeline feature, which is
exactly what this project used to do.

**Bodies are named by blob filename.** The design graph refers to its B-Rep bodies as
`BREP.<uuid>.smb`, written in UTF-16LE (ASCII in older documents). That string is the
link between a component in the timeline and a file in `Breps.BlobParts`, and ezf3d
tests that the set of references equals the set of blobs exactly — which cross-validates
the layout scan and the design-stream scan against each other.

### Parameters

Every dimension a designer types lands in a parameter, and these are the first bulk
objects ezf3d decodes field by field. Two objects hold the index, side by side:

**The manager** carries a reference to the table, then one reference per parameter in id
order. **The table** is `u32 count` followed by that many entries of
`wstr name, 0x01, u64 object id, u16 0`. It is authoritative — every name a design has,
including any the user typed rather than Fusion generating.

The table has no fixed object id (17 in Robotic_Bhujha, 20 in SUCKER, 201 in the wheel),
so it is found by shape: the object whose first wide string is preceded by a count that
walks cleanly to that many `name, reference` entries. The manager is always the object
immediately before it.

**Each parameter is its own object**, laid out after a 31-byte preamble:

```
u32 number                          at +16 — the N in the auto name dN
wstr expression                     at +31 — "300 mm", "1.5 in / 2", "d154"
<padding>                           nine bytes; ten before revision 489
wstr role, wstr comment, wstr unit, wstr name
f64 value
0x00, 0x01, u64 manager, 0x00, 0x00
str8 revision                       of the record, not of the stream
```

The *role* is the slot the parameter fills in the feature that owns it — `AlongDistance`,
`TaperAngle`, `RotateAngle`, `Radius`, `countU` — or, for a sketch dimension, the
dimension's own name (`Diameter Dimension-2`).

**Values are in Fusion's internal units: centimetres and radians.** `300 mm` is stored as
30.0 and `180.0 deg` as pi.

Four redundancies make the reading checkable rather than merely plausible. The name
inside a record equals the name the table filed it under; the `u32` at +16 equals the
digits of that name; the back-reference after the value is the same manager object for
every parameter of a document; and where an expression is a literal, converting it by the
unit gives back the stored value. All four hold for **1,193 of 1,193** parameters across
the four samples — 2, 79, 417 and 695 — including 1,185 literal expressions.

| design | parameters | with a formula |
|---|---|---|
| Mk1 Focuser, Wheel 2 | 2 | 0 |
| SUCKER | 79 | 0 |
| Robotic_Bhujha | 417 | 0 |
| Focuser Mk1 (3 documents) | 695 + 0 + 0 | 8 |

Formulas are reported as written; ezf3d does not evaluate them. They are nonetheless
consistent with what the records store: `d155` is written as `d154` and holds `d154`'s
value, and `1.5 in / 2` is stored as 1.905 — the only measurement pinning the inch
factor.

Two of the package's members declare no table and hold no parameter-like string at all.
Both are documents assembled out of imported bodies, so "none" is the honest answer
rather than a failed search.

### Materials

The design stream stores *assignments*, not materials. The materials themselves live in
the asset's `.protein` package — `ProteinAssets.BlobParts/*.protein`, a nested ZIP of
Autodesk Protein assets.

An assignment is eleven bytes and four wide strings:

```
0x01 0x01 <u64 0> 0x00     a flagged reference
wstr asset       the protein asset id, "0C7D1000-E2AC-D0B5-40B5-F6DFEEDF746D"
wstr library     the library it came from, or "" when the next slot names it
wstr material    "i4 Custom Materials|PLA" — written only for a user library
wstr appearance  "PrismMaterial-018"
```

Those eleven bytes read two ways and the samples cannot separate them: a flag byte and a
reference to object 0 (`AssetSettings`), or a reference to object 1
(`ProteinAssetManager`) and two spare bytes. All six documents number those roots 0 and 1,
so both readings hold everywhere; ezf3d matches the signature literally and claims nothing
about which object it names. The anchor also matches runs of zero padding — SUCKER's
stream holds 22 of them and 13 assignments — so a run counts only when all four slots read
and the first is an asset id.

**Only components and design bodies carry one**, and the holders partition exactly:

| design | components | bodies under `BodiesRoot` | anything else |
|---|---|---|---|
| SUCKER | 1 | 12 | 0 |
| Robotic_Bhujha | 11 | 33 | 0 |
| Focuser Mk1 (3 documents) | 7 | 138 | 0 |

The wheel is the one exception, and only because it has no `BodiesRoot` at all; its two
non-component holders are the base feature's body records.

The package's `AssetData/AssetTableOfContents.bin` is a flat run of `str8` values — the
same length-prefixed encoding the Neutron streams use — pairing each asset id with a
category: `physicalmaterial`, `materialappearance`, `Thermal`, `Structural`. That gives
two checks across different members of the archive, and both hold for all six documents:
**every asset an assignment names is one the package declares**, and every one is a
`physicalmaterial` — the appearance travels as a name in the fourth slot rather than as an
id. **Every appearance name the design writes appears in the package too.**

What is *not* read is the readable name. `PrismMaterial-018` is a Prism appearance id, not
"Steel"; the display name sits in the package's `InstanceProperties.bin` and
`DefinitionIteratorProperties.bin`, which need the schema documents beside them to decode.
A user-library material is the exception — Fusion writes `i4 Custom Materials|PLA` into the
design itself.

Decoding the *contents* of a bulk object — sketch entities and constraints, the ordered
feature timeline, feature payloads — still needs a schema-versioned
decoder per subsystem revision. What the index removes is the need to find them: each
object's id, offset and extent are known, so a decoder can be written for one type at a
time and tested on exactly the bytes of one record. That is the rest of Phase 3.
