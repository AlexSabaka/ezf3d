"""Turning B-Rep geometry into points, lines and triangles."""

from ezf3d.mesh.mesh import Mesh
from ezf3d.mesh.polyline import (
    DEFAULT_CHORD_TOLERANCE,
    MAX_SEGMENTS,
    Wireframe,
    discretise_edge,
    edge_range,
    wireframe,
)
from ezf3d.mesh.tessellate import Tessellation, ear_clip, tessellate, tessellate_face

__all__ = [
    "DEFAULT_CHORD_TOLERANCE",
    "MAX_SEGMENTS",
    "Mesh",
    "Tessellation",
    "Wireframe",
    "discretise_edge",
    "ear_clip",
    "edge_range",
    "tessellate",
    "tessellate_face",
    "wireframe",
]
