"""Turning B-Rep geometry into points, lines and (from Phase 2.3) triangles."""

from ezf3d.mesh.polyline import (
    DEFAULT_CHORD_TOLERANCE,
    MAX_SEGMENTS,
    Wireframe,
    discretise_edge,
    edge_range,
    wireframe,
)

__all__ = [
    "DEFAULT_CHORD_TOLERANCE",
    "MAX_SEGMENTS",
    "Wireframe",
    "discretise_edge",
    "edge_range",
    "wireframe",
]
