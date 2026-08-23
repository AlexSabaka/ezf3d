# The Fusion 360 `.f3d` format

Notes from reverse-engineering Autodesk Fusion 360's native design files. Everything
here was derived by reading real documents, and every claim is exercised by a test in
[`tests/`](../../tests) against the sample designs.

| Document | What it covers |
|---|---|
| [container.md](container.md) | The ZIP shell, Zstandard entries, `.f3z` packages |
| [neutron-streams.md](neutron-streams.md) | The length-prefixed serializer, manifests, segment streams |
| [asm.md](asm.md) | `ASM BinaryFile` — the Shape Manager B-Rep bodies |
| [graphics-cache.md](graphics-cache.md) | The OGS display mesh — Fusion's own tessellation |
| [unknowns.md](unknowns.md) | What is still opaque, and what it would unlock |

## Shape of a document

```
Design.f3d                                  ZIP, entries Zstandard-compressed
├── Manifest.dat                            document identity + schema versions
├── Properties.dat                          JSON blob, usually "{}"
├── ComponentReferenceData.json             usually "{}"
└── FusionAssetName[Active]/                an *asset folder*
    ├── Manifest.dat                        asset identity + segment table
    ├── FusionDesignSegmentType1/           a *segment*
    │   ├── MetaStream.dat                  index
    │   └── BulkStream.dat                  payload — the parametric timeline
    ├── FusionACTSegmentType1/              assembly context tree
    ├── FusionBrowserSegmentType1/          browser tree
    ├── Breps.BlobParts/
    │   ├── BREP.<uuid>.smb                 ASM body
    │   └── BREP.<uuid>.smbh                ASM body carrying rollback history
    ├── ProteinAssets.BlobParts/*.protein   materials (a nested ZIP)
    ├── OGS.BlobFolder/OGS/DefaultScene/    tessellated display mesh — optional
    ├── Previews/small.png                  thumbnail
    ├── Images.BlobParts/                   decals and textures
    └── DesignConfigurationTable.BlobParts/ configurations
```

A document may hold more than one asset folder: an animation lives beside the design as
its own `Animation/` asset with its own segments, and names its parent design by GUID.

## Naming is not stable; types are

Segment folder names drift between Fusion versions — the design segment is
`FusionDesignSegmentType1` in one document and `Design1` in another. The asset manifest's
segment table maps folder prefix to segment *type*, and that mapping is what to key off.
Everything in ezf3d resolves segments by type for this reason.

## Units

Fusion's kernel works in **centimetres**. The design stream carries a `CmMKS` unit system
described in the file itself as "cm modeling length with MKS (m, kg, second, Celsius)
units". Coordinates in ASM bodies are therefore cm, not mm.
