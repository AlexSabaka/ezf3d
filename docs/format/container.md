# The container

A `.f3d` is a ZIP file. So is a `.f3z`. Neither can be opened by stock tooling, for one
reason.

## Zstandard entries

Fusion compresses entries with **ZIP method 93 — Zstandard** (APPNOTE 6.3.7). Python's
`zipfile` parses the central directory of such an archive without complaint and raises
only from `ZipFile.open()`:

```python
>>> zipfile.ZipFile("Design.f3d").read("Manifest.dat")
NotImplementedError: compression type 93 (zstd)
```

This is why existing tooling fails on files saved today: the ZIP is readable, the data is
not. `unzip -v` reports the method as `Unk:093`.

Only a few entries escape it — zero-byte files and two-byte JSON stubs are `Stored`,
since compressing them would make them larger. A `.f3z` wrapper uses ordinary `Deflate`;
the `.f3d` members inside it are zstd.

**How ezf3d reads it** ([`container/archive.py`](../../src/ezf3d/container/archive.py)):
let `zipfile` own directory parsing, then seek to the entry's `header_offset`, skip the
30-byte local header plus its name and extra fields, read `compress_size` bytes from the
central directory's figure, and inflate them directly. The local header's own size fields
are unreliable when general-purpose bit 3 (data descriptor) is set, so they are ignored.
Decompression uses `read_across_frames=True` because a large entry can span several zstd
frames.

Every read is CRC-checked against the central directory, which is what lets
`ezf3d dump` claim byte-for-byte parity with a reference extraction.

`F3DArchive.read_prefix(name, n)` inflates only the first `n` bytes — enough to read a
body's ASM header without decompressing 25 MB behind it.

## `.f3z` packages

A `.f3z` is a flat ZIP of one `.f3d` per referenced design, plus two JSON sidecars:

**`Manifest.json`** names the entry document:

```json
{"root": "67727a39-fa29-49f6-849f-35e15bdf1231.f3d"}
```

**`DesignDescription.json`** is Autodesk's *Design Description* graph — every member with
its friendly name, cloud URN, lineage, and its XREF relationships:

```json
{"designDescription": {"designGraphs": [{"rootIds": [637306], "designObjects": [
  {"id": 637306, "friendlyName": "Focuser Mk1",
   "relativePath": "67727a39-….f3d",
   "references": [{"type": "XREF", "ids": [637307, 637308]}]}
]}]}}
```

Members are ordinary `.f3d` documents and are opened from memory rather than being
written to disk. Note that a member produced by Autodesk's cloud translator predates
some schema changes — see [neutron-streams.md](neutron-streams.md#version-blocks).
