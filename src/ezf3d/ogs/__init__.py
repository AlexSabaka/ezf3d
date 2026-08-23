"""Reading the OGS graphics cache — Fusion's own tessellation of a design.

The cache is optional: some documents carry it, some do not, and where it is
present it may cover only part of a body.  :mod:`ezf3d.ogs.verify` is how far
it can be trusted gets measured rather than assumed.
"""

from ezf3d.ogs.cache import CachedFace, GraphicsCache, read_cache
from ezf3d.ogs.stream import OgsError
from ezf3d.ogs.verify import Agreement, compare, hausdorff, match_faces
from ezf3d.ogs.world import Buffer, World, read_world

__all__ = [
    "Agreement",
    "Buffer",
    "CachedFace",
    "GraphicsCache",
    "OgsError",
    "World",
    "compare",
    "hausdorff",
    "match_faces",
    "read_cache",
    "read_world",
]
