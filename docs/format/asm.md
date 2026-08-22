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
32-bit and parses with the same code. The version word tracks the release: `23200` for
ASM 232, `23100` for 231.

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

**Pointers are record indices** — `POINTER=13` means entity 13 in this file, and `-1` is
null. The whole file is a flat array with an implicit graph over it, so resolution is a
list lookup.

The stream ends with `End of ASM data`. In a `.smbh` the history marker can share a
record with it, without an intervening terminator, so neither test may exclude the other.
`AsmModel.terminated` records whether the walk actually reached the end — a body that
parses without error but stops short was only partly understood.

## Topology and geometry

The ACIS hierarchy, unchanged:

```
body → lump → shell → face → loop → coedge → edge → vertex → point
```

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
record's extent is knowable without understanding the geometry inside it — which is why
the whole file walks even though the splines are not yet evaluated.

## Attributes

`ATTRIB_CUSTOM` / `attrib` records carry Fusion's own tags:
`generic_tag_attrib_def` (integer tags that survive rebuilds and are how the timeline
addresses individual faces and edges) and `Timestamp_attrib_def` (rollback bookkeeping).
Decoding the tag attributes is what will let a feature reference like "fillet these two
edges" be resolved to actual topology.
