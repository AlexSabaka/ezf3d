"""What a ``world`` says about the geometry Fusion cached.

A scene node that owns geometry carries a fixed descriptor 45 bytes past its
name::

    u32 0x800          "a buffer follows"; a node without one holds 0 here
    u32 kind           6 = polyline, 7 = triangles
    u32 0              zero throughout; most likely which blob, of the one there is
    u32 offset         byte offset into the vertex blob
    u32 position       floats of position data      (3 per vertex)
    u32 normal         floats of normal data        (3 per vertex)
    u32 texture        floats of texture data       (2 per vertex)  [kind 7]
    u32 indices        index count                  (3 per triangle) [kind 7]
    u32 n, n x u32     the edges bounding this face                  [kind 7]
    6 x f64            the node's bounding box, in kernel centimetres

so a face's vertices are ``position / 3`` interleaved ``P3 N3 T2`` float32 —
32 bytes each — immediately followed by its ``uint32`` indices, and an edge's
are ``P3 N3``, 24 bytes each, with no indices.

**The same node is written more than once.**  A ``world`` holds several render
lists over the same body; only the first carries buffers and the rest set the
flag word to zero and refer back.  Reading every flagged node and no others is
what makes the descriptors tile the blob exactly.

The bounding box is not needed to read the mesh, and is read anyway: a
node that states where its geometry is *supposed* to be is a node that can be
checked against where it turned out to be.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ezf3d.ogs.stream import Node, walk

#: Distance from the end of a node's name to its buffer descriptor.
DESCRIPTOR_OFFSET = 45

#: Value of the descriptor's first word when geometry follows.
HAS_BUFFER = 0x800

#: Descriptor kinds this module knows how to read.
TRIANGLES = 7
POLYLINE = 6

#: Bytes per vertex, by kind: ``P3 N3 T2`` and ``P3 N3``.
_STRIDE = {TRIANGLES: 32, POLYLINE: 24}

_HEADER = struct.Struct("<3I")
_SPAN = struct.Struct("<3I")
_TRAILER = struct.Struct("<2I")


@dataclass(frozen=True, slots=True)
class Buffer:
    """One node's slice of the vertex blob."""

    #: Node name — ``Face``, ``Edge``, ``ProfileFace``, ``ProfileEdge``.
    owner: str
    kind: int
    offset: int
    vertices: int
    indices: int
    #: The bounding box the node states, or ``None`` if it could not be read.
    box: tuple[np.ndarray, np.ndarray] | None

    @property
    def stride(self) -> int:
        return _STRIDE[self.kind]

    @property
    def size(self) -> int:
        """Bytes this buffer occupies, vertices and indices together."""
        return self.vertices * self.stride + self.indices * 4

    @property
    def triangles(self) -> int:
        return self.indices // 3


@dataclass(slots=True)
class World:
    """The geometry a ``world`` names, in file order."""

    buffers: list[Buffer] = field(default_factory=list)
    #: Objects walked, by name — including those that carry nothing.
    census: Counter[str] = field(default_factory=Counter)
    #: Nodes carrying ``0x800`` at the descriptor offset whose descriptor did
    #: not read out.  Every one seen so far is a ``SketchCurve`` whose record
    #: ends before a descriptor would — the flag word is a coincidence in
    #: sketch data rather than a buffer this module is failing to find.  None
    #: of them names any part of the vertex blob, which is why
    #: :meth:`covers` still comes out exact.  Counted, never assumed away.
    unread: int = 0

    def of_kind(self, owner: str) -> list[Buffer]:
        return [buffer for buffer in self.buffers if buffer.owner == owner]

    @property
    def faces(self) -> list[Buffer]:
        return self.of_kind("Face")

    @property
    def edges(self) -> list[Buffer]:
        return self.of_kind("Edge")

    def covers(self, blob_size: int) -> tuple[int, int]:
        """``(gap, overlap)`` bytes when the buffers are laid over a blob.

        Both are zero for a cache this module has read completely, which is
        the sharpest check available on the walk: a missed node leaves a gap
        and an invented one an overlap.
        """
        spans = sorted((buffer.offset, buffer.offset + buffer.size) for buffer in self.buffers)
        gap = overlap = 0
        reach = 0
        for start, stop in spans:
            if start > reach:
                gap += start - reach
            else:
                overlap += min(reach - start, stop - start)
            reach = max(reach, stop)
        return gap + max(0, blob_size - reach), overlap


def _read_box(data: bytes, pos: int) -> tuple[np.ndarray, np.ndarray] | None:
    if pos + 48 > len(data):
        return None
    values = np.frombuffer(data, dtype="<f8", count=6, offset=pos)
    if not np.isfinite(values).all():
        return None
    lower, upper = values[:3].copy(), values[3:].copy()
    return (lower, upper) if bool((lower <= upper).all()) else None


def _read_descriptor(data: bytes, node: Node, blob_size: int) -> Buffer | None:
    """The buffer *node* declares, or ``None`` if it declares none."""
    head = node.payload + DESCRIPTOR_OFFSET
    if head + _HEADER.size > node.end:
        return None
    flags, kind, blob = _HEADER.unpack_from(data, head)
    if flags != HAS_BUFFER:
        return None
    # `blob` is zero in every sample.  A document large enough to spill into a
    # second ``Fusion_mesh_00N`` would be the way to find out whether it is the
    # blob index it looks like; until then, a non-zero value is not understood
    # and the node is left unread rather than guessed at.
    if kind not in _STRIDE or blob:
        return None

    span = head + _HEADER.size
    if span + _SPAN.size > len(data):
        return None
    offset, position, normal = _SPAN.unpack_from(data, span)
    # Position and normal are three floats per vertex and must agree.
    if position != normal or position % 3:
        return None
    count = position // 3

    indices = 0
    tail = span + _SPAN.size
    if kind == TRIANGLES:
        if tail + _TRAILER.size > len(data):
            return None
        texture, indices = _TRAILER.unpack_from(data, tail)
        # Texture coordinates are two floats per vertex.
        if texture != 2 * count or indices % 3:
            return None
        tail += _TRAILER.size
        bounded = struct.unpack_from("<I", data, tail)[0] if tail + 4 <= len(data) else 0
        tail += 4 + 4 * bounded

    buffer = Buffer(
        owner=node.name,
        kind=kind,
        offset=offset,
        vertices=count,
        indices=indices,
        box=_read_box(data, tail),
    )
    return buffer if buffer.offset + buffer.size <= blob_size else None


def read_world(data: bytes, blob_size: int) -> World:
    """Every buffer *data* names, checked against a blob of *blob_size* bytes."""
    world = World()
    for node in walk(data):
        world.census[node.name] += 1
        head = node.payload + DESCRIPTOR_OFFSET
        if head + 4 > node.end:
            continue
        if struct.unpack_from("<I", data, head)[0] != HAS_BUFFER:
            continue
        buffer = _read_descriptor(data, node, blob_size)
        if buffer is None:
            world.unread += 1
        else:
            world.buffers.append(buffer)
    return world
