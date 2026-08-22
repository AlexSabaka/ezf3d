"""A pure-numpy line rasteriser with depth sorting.

No OpenGL, no native extension: rendering a design has to work over SSH, in a
container, and on whatever machine an agent happens to be on.  Everything here
is array arithmetic.

Hidden-line removal is not attempted — that needs the tessellated faces Phase
2.3 will produce.  Instead lines are **depth-cued**: nearer edges are drawn
darker and stronger than far ones, which reads well enough that the shape of a
part is legible without solid faces.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ezf3d.render.camera import Camera

#: Rendering is done at this multiple of the requested size and box-filtered
#: down, which is what gives the lines their anti-aliasing.
DEFAULT_SUPERSAMPLE = 3


@dataclass(slots=True)
class Style:
    """Colours and weights for a wireframe render."""

    background: tuple[int, int, int] = (250, 250, 248)
    #: Colour of the nearest edges.
    near: tuple[int, int, int] = (20, 30, 45)
    #: Colour of the farthest edges — depth cueing fades towards this.
    far: tuple[int, int, int] = (170, 180, 195)
    #: Line half-width in supersampled pixels; 1 gives a solid hairline.
    weight: int = 1


def _stamp(weight: int) -> np.ndarray:
    """Pixel offsets that give a line its width."""
    span = np.arange(-weight, weight + 1)
    dx, dy = np.meshgrid(span, span, indexing="ij")
    keep = (dx**2 + dy**2) <= weight**2 + 0.25
    return np.stack([dx[keep], dy[keep]], axis=1)


def _sample_segments(
    xy: np.ndarray, depth: np.ndarray, keep: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Walk every kept segment one pixel at a time.

    Returns the sample positions and their depths.  Step counts vary per
    segment, so the walk is expressed with ``repeat``/``cumsum`` rather than a
    loop.
    """
    start, end = xy[keep, 0], xy[keep, 1]
    d0, d1 = depth[keep, 0], depth[keep, 1]
    if not len(start):
        return np.zeros((0, 2)), np.zeros(0)

    steps = np.ceil(np.abs(end - start).max(axis=1)).astype(np.int64) + 1
    np.clip(steps, 1, 1 << 16, out=steps)
    total = int(steps.sum())

    segment = np.repeat(np.arange(len(steps)), steps)
    offset = np.arange(total) - np.repeat(np.cumsum(steps) - steps, steps)
    t = offset / np.maximum(steps[segment] - 1, 1)

    points = start[segment] + t[:, None] * (end - start)[segment]
    depths = d0[segment] + t * (d1 - d0)[segment]
    return points, depths


def render_lines(
    segments: np.ndarray,
    camera: Camera,
    *,
    style: Style | None = None,
    supersample: int = DEFAULT_SUPERSAMPLE,
) -> np.ndarray:
    """Rasterise ``(n, 2, 3)`` world-space line segments to an RGB image."""
    style = style or Style()
    width, height = camera.width * supersample, camera.height * supersample
    canvas = np.empty((height * width, 3), dtype=np.float32)
    canvas[:] = np.array(style.background, dtype=np.float32)

    segments = np.asarray(segments, dtype=float)
    if not len(segments):
        return _resolve(canvas, height, width, supersample)

    big = camera.__class__(
        eye=camera.eye,
        target=camera.target,
        up=camera.up,
        width=width,
        height=height,
        scale=camera.scale,
        perspective=camera.perspective,
        fov=camera.fov,
    )
    flat_xy, flat_depth = big.project(segments.reshape(-1, 3))
    xy = flat_xy.reshape(-1, 2, 2)
    depth = flat_depth.reshape(-1, 2)

    # Drop segments behind the eye, and those wholly outside the frame.
    in_front = (depth > 0).all(axis=1) if camera.perspective else np.ones(len(xy), bool)
    lo, hi = xy.min(axis=1), xy.max(axis=1)
    on_screen = (hi[:, 0] >= 0) & (lo[:, 0] < width) & (hi[:, 1] >= 0) & (lo[:, 1] < height)
    keep = in_front & on_screen
    if not keep.any():
        return _resolve(canvas, height, width, supersample)

    points, depths = _sample_segments(xy, depth, keep)

    stamp = _stamp(style.weight)
    px = np.rint(points[:, None, 0] + stamp[None, :, 0]).astype(np.int64).ravel()
    py = np.rint(points[:, None, 1] + stamp[None, :, 1]).astype(np.int64).ravel()
    pd = np.repeat(depths, len(stamp))

    inside = (px >= 0) & (px < width) & (py >= 0) & (py < height)
    px, py, pd = px[inside], py[inside], pd[inside]
    if not len(px):
        return _resolve(canvas, height, width, supersample)

    near, far = float(pd.min()), float(pd.max())
    span = far - near
    fade = (pd - near) / span if span > 1e-12 else np.zeros_like(pd)
    colour = np.array(style.near, dtype=np.float32) + fade[:, None] * (
        np.array(style.far, dtype=np.float32) - np.array(style.near, dtype=np.float32)
    )

    # Nearest sample wins each pixel.  Sorting by depth and taking the first
    # occurrence per pixel is a depth test without a per-pixel loop.
    flat = py * width + px
    order = np.argsort(pd, kind="stable")
    unique_pixels, first = np.unique(flat[order], return_index=True)
    canvas[unique_pixels] = colour[order[first]]

    return _resolve(canvas, height, width, supersample)


def _resolve(canvas: np.ndarray, height: int, width: int, supersample: int) -> np.ndarray:
    """Box-filter the supersampled buffer down to the requested size."""
    image = canvas.reshape(height, width, 3)
    if supersample > 1:
        image = image.reshape(
            height // supersample, supersample, width // supersample, supersample, 3
        ).mean(axis=(1, 3))
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


def ink_bounds(
    image: np.ndarray, background: tuple[int, int, int]
) -> tuple[int, int, int, int] | None:
    """Bounding box of everything that is not background: ``(x0, y0, x1, y1)``.

    Used to check a render against the geometry it claims to show.
    """
    reference = np.array(background, dtype=np.int16)
    marked = np.abs(image.astype(np.int16) - reference).sum(axis=2) > 12
    if not marked.any():
        return None
    rows = np.flatnonzero(marked.any(axis=1))
    cols = np.flatnonzero(marked.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])
