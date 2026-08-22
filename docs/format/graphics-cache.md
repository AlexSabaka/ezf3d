# The OGS graphics cache

**Status: partially decoded.** Not used by ezf3d yet; documented here because it is the
cheapest route to a rendered image and the notes should not be lost.

Fusion sometimes leaves its tessellated display mesh in the document, under
`<Asset>[Active]/OGS.BlobFolder/OGS/DefaultScene/`. OGS is Autodesk's *One Graphics
System*.

```
world              UTF-16LE scene graph
Fusion_mesh_000    concatenated vertex/index buffers
stream_mesh_000    a smaller buffer in the same encoding
```

## What is known

`stream_mesh_000` decodes cleanly as **interleaved `P3 N3 T2` float32, stride 32 bytes** —
position, normal, UV — followed by `uint32` indices. The first quad of one sample:

```
(-24,-24,12) n(0,0,1) uv(0,1)     (24,-24,12) n(0,0,1) uv(1,1)
( 24, 24,12) n(0,0,1) uv(1,0)     (-24, 24,12) n(0,0,1) uv(0,0)
indices: 0,1,2  2,3,0
```

`Fusion_mesh_000` is **not** uniform-stride. It is a concatenation of buffers with
differing vertex formats, indexed by offset, length and declaration from `world`.

`world` opens with a `wstr` root name and a table of class names and counts:

```
ARenderList, GroupNode, TextureMappingAttribute, TextureEffectAttribute,
NodeData, GraphicsBlobFeatures, F360InstanceNode, BillBoardAttribute,
GraphicsGlobalVersion, OGSBlobVersion, FColorEffectAttribute, Edges, Edge,
SingleNodeWorld, DarkSky
```

interleaved with node handles written as hex strings (`0x3a94700f8`). `GraphicsBlobFeatures`
is the record type that must carry the buffer descriptors.

## Why it is not the primary render path

**The folder is optional.** Of the four sample designs, three have it and one — a
22-body assembly — has none at all. A renderer that depends on it would silently fail on
real files.

The plan is therefore to tessellate ASM surfaces directly, with OGS as an opportunistic
fast path where present. Analytic surfaces cover a large fraction of real bodies already
(see [asm.md](asm.md#topology-and-geometry)).
