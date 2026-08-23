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

**`MetaStream.dat`** — the index:

```
str8   prefix                folder prefix, matching the manifest declaration
u32    slot                  matching the manifest declaration
wstr   guid                  all-zero for a segment that has never branched
       <version block>
str8   declared_type         "FusionDesignSegmentType" (plain "Design" in old files)
str8   owner                 "Fusion" / "Animation"; empty in old files
       ... per-module records naming object ids in the bulk stream
```

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

### The type names are a dictionary, not a timeline

They arrive in **registries**: one entry per kind, sorted by name, that the objects index
into. SUCKER holds one of 17 entries at offset 9558; Robotic_Bhujha holds eleven, of 2 to
14 entries each.

Two measurements say a *count* of these names describes the dictionary rather than the
design. No registry repeats a name, and a name's total across the stream is exactly the
number of registries that declare it — so Robotic_Bhujha's nine
`DcExtrudeFeatureMetaType` are nine registries that permit an extrude, not nine extrudes.

`ezf3d` therefore reports the declared *set* and how many registries there are, and says
nothing about how many features a design has, because at this level the stream does not
say.

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

Alongside those sit Fusion's auto-named dimension parameters (`d1`, `d3`, `d73`), stored
as UTF-16LE with a `u32` character count — 834 of them in Robotic_Bhujha — and the unit
names their expressions carry (`mm` ×283, `deg` ×110).

**Bodies are named by blob filename.** The design graph refers to its B-Rep bodies as
`BREP.<uuid>.smb`, written in UTF-16LE (ASCII in older documents). That string is the
link between a component in the timeline and a file in `Breps.BlobParts`, and ezf3d
tests that the set of references equals the set of blobs exactly — which cross-validates
the layout scan and the design-stream scan against each other.

Decoding individual bulk records — parameters with their expressions, sketch entities and
constraints, the ordered feature timeline — needs a schema-versioned decoder per
subsystem revision, and the meta stream is no shortcut: it indexes by logical object id,
not by byte offset. Of 511 string offsets in the wheel's bulk stream, three appear
anywhere in its meta stream, which is chance. So the payload has to be read sequentially.
That is the rest of Phase 3; until then it is reachable as raw bytes.
