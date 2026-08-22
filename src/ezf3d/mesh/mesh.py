"""A triangle mesh, and the questions worth asking of one."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(slots=True)
class Mesh:
    """Shared-vertex triangle soup in kernel units (cm)."""

    vertices: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    triangles: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), dtype=np.int64))

    def __len__(self) -> int:
        return len(self.triangles)

    @property
    def is_empty(self) -> bool:
        return not len(self.triangles)

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        if not len(self.vertices):
            return None
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    def corners(self) -> np.ndarray:
        """``(m, 3, 3)`` — every triangle's three vertices."""
        return self.vertices[self.triangles]

    def face_normals(self) -> np.ndarray:
        """Unit normal per triangle, from its winding."""
        a, b, c = np.moveaxis(self.corners(), 1, 0)
        normals = np.cross(b - a, c - a)
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        return np.divide(normals, np.where(lengths < 1e-30, 1.0, lengths))

    def areas(self) -> np.ndarray:
        a, b, c = np.moveaxis(self.corners(), 1, 0)
        return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

    def area(self) -> float:
        return float(self.areas().sum())

    def volume(self) -> float:
        """Signed volume by the divergence theorem.

        Only meaningful for a closed, consistently wound mesh — which is what
        :meth:`edge_use_counts` is for.
        """
        a, b, c = np.moveaxis(self.corners(), 1, 0)
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)

    def edge_use_counts(self) -> dict[int, int]:
        """How many triangles use each undirected edge, tallied by count.

        ``{2: n}`` alone means closed and manifold; a ``1`` is a boundary or a
        crack, a ``3`` or more is a non-manifold junction.
        """
        if self.is_empty:
            return {}
        edges = np.concatenate(
            [self.triangles[:, [0, 1]], self.triangles[:, [1, 2]], self.triangles[:, [2, 0]]]
        )
        edges = np.sort(edges, axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        values, tally = np.unique(counts, return_counts=True)
        return {int(v): int(t) for v, t in zip(values, tally, strict=True)}

    @property
    def is_watertight(self) -> bool:
        return self.edge_use_counts() == {2: sum(self.edge_use_counts().values())}

    def merged(self, other: Mesh) -> Mesh:
        """Concatenate, keeping vertices distinct."""
        if other.is_empty:
            return self
        if self.is_empty:
            return other
        return Mesh(
            vertices=np.concatenate([self.vertices, other.vertices]),
            triangles=np.concatenate([self.triangles, other.triangles + len(self.vertices)]),
        )

    def cleaned(self) -> Mesh:
        """Drop triangles that carry no area, and repeats of the same triangle.

        Bridging a hole into its outer loop walks the cut in both directions,
        which leaves the ear clipper a few slivers of zero width and the odd
        repeated ear.  Left in, each repeat makes its edges look non-manifold.
        """
        if self.is_empty:
            return self
        keep = self.areas() > 0.0
        triangles = self.triangles[keep]
        if not len(triangles):
            return Mesh(vertices=self.vertices)
        # Repeats are matched with their winding intact.  Two triangles on the
        # same three vertices but wound opposite ways are the two sides of a
        # bridge cut, not a duplicate — discarding one leaves a hole.
        rolled = np.stack([np.roll(triangles, shift, axis=1) for shift in (0, 1, 2)], axis=0)
        canonical = rolled[np.argmin(rolled[:, :, 0], axis=0), np.arange(len(triangles))]
        _, first = np.unique(canonical, axis=0, return_index=True)
        return Mesh(vertices=self.vertices, triangles=triangles[np.sort(first)])

    def welded(self, tolerance: float = 1e-9) -> Mesh:
        """Fuse vertices that coincide, so shared edges become shared indices."""
        if self.is_empty:
            return self
        quantised = np.round(self.vertices / tolerance).astype(np.int64)
        _, first, inverse = np.unique(quantised, axis=0, return_index=True, return_inverse=True)
        remapped = self.triangles.reshape(-1)
        vertices = self.vertices[first]
        # np.unique sorts; `inverse` already maps old index -> new index.
        triangles = inverse.reshape(-1)[remapped].reshape(-1, 3)
        keep = (
            (triangles[:, 0] != triangles[:, 1])
            & (triangles[:, 1] != triangles[:, 2])
            & (triangles[:, 2] != triangles[:, 0])
        )
        return Mesh(vertices=vertices, triangles=triangles[keep])
