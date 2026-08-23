"""Fusion's own tessellation, read back out of the document.

Where the ASM tessellation in :mod:`ezf3d.mesh.tessellate` derives triangles
from surfaces, this reads the ones Fusion itself drew.  The two are worth
having side by side for opposite reasons: the ASM route works on every body
and is only as good as ezf3d's geometry, while the cache is exactly what
Fusion displayed but is present in some files, absent in others, and covers
only what was on screen when the design was saved.

Three properties decide how far it can be trusted, and all three are measured
rather than assumed — see :mod:`ezf3d.ogs.verify`:

**It is one body's worth.**  A ``world`` holds several render lists over the
same body; only the first carries buffers.  In one sample that is the whole
body, 423 faces of 423.  In the other it is 608 faces of 2006 — a partial
cache, and no error.

**It is in kernel units.**  Cached vertices sit within 3e-07 cm of the
analytic surfaces read from the same document's ASM, so the two coordinate
systems are the same one.

**It is wound outwards** and welds to a closed manifold, which is more than
the ASM tessellation manages on a spline-heavy body.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ezf3d.mesh.mesh import Mesh
from ezf3d.ogs.stream import OgsError, check_magic
from ezf3d.ogs.world import POLYLINE, TRIANGLES, Buffer, World, read_world

#: Cached vertices are float32, so coincident ones differ in the last bits.
#: Welding at a micron fuses them without merging anything real: Fusion's own
#: chord tolerance is orders of magnitude coarser.
WELD_TOLERANCE = 1e-4

WORLD = "world"
_BLOB_PREFIX = "Fusion_mesh_"


@dataclass(frozen=True, slots=True)
class CachedFace:
    """One face's triangles, as Fusion tessellated them."""

    points: np.ndarray
    normals: np.ndarray
    triangles: np.ndarray
    #: The bounding box the scene node states for this face, if it stated one.
    box: tuple[np.ndarray, np.ndarray] | None

    def mesh(self) -> Mesh:
        return Mesh(vertices=self.points, triangles=self.triangles)

    def centroid(self) -> np.ndarray:
        return self.points.mean(axis=0)


class GraphicsCache:
    """The ``OGS.BlobFolder`` of one asset, read into meshes."""

    __slots__ = ("_blob", "world")

    def __init__(self, world_data: bytes, blob: bytes) -> None:
        check_magic(world_data)
        self._blob = blob
        self.world: World = read_world(world_data, len(blob))

    # -- what is here ------------------------------------------------------

    @property
    def face_count(self) -> int:
        return len(self.world.faces)

    @property
    def edge_count(self) -> int:
        return len(self.world.edges)

    @property
    def triangle_count(self) -> int:
        return sum(buffer.triangles for buffer in self.world.faces)

    @property
    def is_empty(self) -> bool:
        return not self.world.buffers

    def coverage(self) -> tuple[int, int]:
        """``(gap, overlap)`` bytes of the vertex blob — zero, zero when read whole."""
        return self.world.covers(len(self._blob))

    # -- geometry ----------------------------------------------------------

    def _vertices(self, buffer: Buffer) -> np.ndarray:
        floats = buffer.vertices * buffer.stride // 4
        flat = np.frombuffer(self._blob, dtype="<f4", count=floats, offset=buffer.offset)
        return flat.reshape(buffer.vertices, buffer.stride // 4).astype(np.float64)

    def faces(self) -> list[CachedFace]:
        """Every cached face, in the order the scene graph lists them."""
        out: list[CachedFace] = []
        for buffer in self.world.faces:
            if buffer.kind != TRIANGLES:
                continue
            block = self._vertices(buffer)
            start = buffer.offset + buffer.vertices * buffer.stride
            indices = np.frombuffer(self._blob, dtype="<u4", count=buffer.indices, offset=start)
            out.append(
                CachedFace(
                    points=block[:, 0:3],
                    normals=block[:, 3:6],
                    triangles=indices.reshape(-1, 3).astype(np.int64),
                    box=buffer.box,
                )
            )
        return out

    def mesh(self, *, weld: float = WELD_TOLERANCE) -> Mesh:
        """Every cached face as one mesh, welded so shared edges are shared."""
        points: list[np.ndarray] = []
        triangles: list[np.ndarray] = []
        base = 0
        for face in self.faces():
            points.append(face.points)
            triangles.append(face.triangles + base)
            base += len(face.points)
        if not points:
            return Mesh()
        merged = Mesh(vertices=np.concatenate(points), triangles=np.concatenate(triangles))
        return merged.welded(weld).cleaned() if weld else merged

    def edge_endpoints(self) -> np.ndarray:
        """First and last point of every cached edge polyline, ``(n, 3)``.

        These are B-Rep vertices: Fusion subdivides an edge between its two
        ends, so the ends themselves are the ``point`` records of the body it
        is drawing.  That makes them a fingerprint for *which* body that is.
        """
        corners: list[np.ndarray] = []
        for buffer in self.world.edges:
            if buffer.kind != POLYLINE or buffer.vertices < 1:
                continue
            points = self._vertices(buffer)[:, 0:3]
            corners.append(points[[0, -1]])
        if not corners:
            return np.zeros((0, 3))
        return np.concatenate(corners)

    def segments(self) -> np.ndarray:
        """Cached edges as ``(n, 2, 3)`` line segments.

        Fusion stores each edge as a polyline of consecutive points, so the
        segments are its successive pairs.
        """
        pieces: list[np.ndarray] = []
        for buffer in self.world.edges:
            if buffer.kind != POLYLINE or buffer.vertices < 2:
                continue
            points = self._vertices(buffer)[:, 0:3]
            pieces.append(np.stack([points[:-1], points[1:]], axis=1))
        if not pieces:
            return np.zeros((0, 2, 3))
        return np.concatenate(pieces)


def find_blobs(names: list[str]) -> tuple[str | None, list[str]]:
    """``(world path, vertex blob paths)`` among an asset's OGS entries."""
    world = next((name for name in names if name.rsplit("/", 1)[-1] == WORLD), None)
    blobs = sorted(name for name in names if name.rsplit("/", 1)[-1].startswith(_BLOB_PREFIX))
    return world, blobs


def read_cache(read: object, names: list[str]) -> GraphicsCache:
    """Open the cache described by *names*, pulling bytes through *read*.

    *read* is any callable taking an archive member name and returning bytes —
    :meth:`ezf3d.container.archive.F3DArchive.read` in practice.
    """
    world, blobs = find_blobs(names)
    if world is None:
        raise OgsError("no world in this graphics cache")
    if not blobs:
        raise OgsError("no vertex blob in this graphics cache")
    if len(blobs) > 1:
        raise OgsError(f"{len(blobs)} vertex blobs; only one is understood")
    return GraphicsCache(read(world), read(blobs[0]))  # type: ignore[operator]
