"""Offscreen rendering — pure numpy, no GL, no native dependencies."""

from ezf3d.render.camera import VIEW_DIRECTIONS, Camera
from ezf3d.render.png import encode, write
from ezf3d.render.raster import DEFAULT_SUPERSAMPLE, Style, ink_bounds, render_lines
from ezf3d.render.scene import Scene, build_scene, contact_sheet

__all__ = [
    "DEFAULT_SUPERSAMPLE",
    "VIEW_DIRECTIONS",
    "Camera",
    "Scene",
    "Style",
    "build_scene",
    "contact_sheet",
    "encode",
    "ink_bounds",
    "render_lines",
    "write",
]
