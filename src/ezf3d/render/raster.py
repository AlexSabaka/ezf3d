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

#: Pixel samples processed at once while filling triangles.  Bounds peak
#: memory: without it a mesh whose triangles each cover much of the frame
#: allocates tens of millions of samples in one go.
_SAMPLE_BUDGET = 4_000_000


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
    #: Surface colour at full illumination, and in shadow.
    lit: tuple[int, int, int] = (214, 219, 226)
    shadow: tuple[int, int, int] = (58, 70, 88)
    #: Direction the key light comes from, in view space (right, up, toward).
    light: tuple[float, float, float] = (-0.35, 0.45, 1.0)
    #: Edge colour when a wireframe is drawn over a shaded surface.
    overlay: tuple[int, int, int] = (30, 38, 52)


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


def render_mesh(
    mesh,
    camera: Camera,
    *,
    style: Style | None = None,
    supersample: int = DEFAULT_SUPERSAMPLE,
    edges: np.ndarray | None = None,
) -> np.ndarray:
    """Rasterise a triangle mesh with a depth buffer and diffuse shading.

    Triangles are covered by testing the barycentric coordinates of every pixel
    in their bounding box — expressed once over all triangles at once, since a
    per-triangle Python loop at supersampled resolution would dominate.

    Passing *edges* draws those line segments over the surface, which is what
    makes a shaded CAD render legible: the silhouette alone hides the feature
    lines that say what the part is.
    """
    style = style or Style()
    width, height = camera.width * supersample, camera.height * supersample
    canvas = np.empty((height * width, 3), dtype=np.float32)
    canvas[:] = np.array(style.background, dtype=np.float32)

    big = _scaled_camera(camera, width, height)
    if mesh is not None and not mesh.is_empty:
        _draw_triangles(canvas, mesh, big, style, width, height)
    if edges is not None and len(edges):
        _draw_overlay(canvas, edges, big, style, width, height)
    return _resolve(canvas, height, width, supersample)


def _scaled_camera(camera: Camera, width: int, height: int) -> Camera:
    return Camera(
        eye=camera.eye,
        target=camera.target,
        up=camera.up,
        width=width,
        height=height,
        scale=camera.scale,
        perspective=camera.perspective,
        fov=camera.fov,
    )


def _shade(mesh, camera: Camera, style: Style) -> np.ndarray:
    """Diffuse colour per triangle, lit from the viewer's shoulder."""
    right, up, backward = camera.basis()
    normals = mesh.face_normals()
    direction = np.array(style.light, dtype=float)
    direction = direction / np.linalg.norm(direction)
    world_light = direction[0] * right + direction[1] * up + direction[2] * backward
    # Two-sided: a face pointing away is still lit, since a mesh with a few
    # inverted windings should read as a shape rather than a black hole.
    intensity = np.abs(normals @ world_light)
    ambient = 0.25
    level = np.clip(ambient + (1.0 - ambient) * intensity, 0.0, 1.0)[:, None]
    lit = np.array(style.lit, dtype=np.float32)
    shadow = np.array(style.shadow, dtype=np.float32)
    return shadow + level * (lit - shadow)


def _draw_triangles(canvas, mesh, camera: Camera, style: Style, width: int, height: int) -> None:
    xy, depth = camera.project(mesh.vertices)
    tri_xy = xy[mesh.triangles].astype(np.float32)
    tri_depth = depth[mesh.triangles].astype(np.float32)
    colour = _shade(mesh, camera, style)

    lo = np.floor(tri_xy.min(axis=1)).astype(np.int64)
    hi = np.ceil(tri_xy.max(axis=1)).astype(np.int64)
    np.clip(lo, [0, 0], [width - 1, height - 1], out=lo)
    np.clip(hi, [0, 0], [width - 1, height - 1], out=hi)
    span = np.maximum(hi - lo + 1, 0)
    counts = (span[:, 0] * span[:, 1]).astype(np.int64)
    if camera.perspective:
        counts = np.where((tri_depth > 0).all(axis=1), counts, 0)
    order = np.flatnonzero(counts > 0)
    if not len(order):
        return

    zbuffer = np.full(height * width, np.inf, dtype=np.float32)

    # A big triangle's bounding box can be most of the frame, so the whole
    # mesh at once is tens of millions of samples and gigabytes of scratch.
    # Chunking to a fixed sample budget keeps memory flat and, because the
    # depth buffer persists between chunks, changes nothing about the result.
    start = 0
    while start < len(order):
        end = start + 1
        budget = int(counts[order[start]])
        while end < len(order) and budget + counts[order[end]] <= _SAMPLE_BUDGET:
            budget += int(counts[order[end]])
            end += 1
        _draw_chunk(
            canvas, zbuffer, order[start:end], counts, lo, span, tri_xy, tri_depth, colour, width
        )
        start = end


def _draw_chunk(canvas, zbuffer, chunk, counts, lo, span, tri_xy, tri_depth, colour, width) -> None:
    index = np.repeat(chunk, counts[chunk])
    sizes = counts[chunk]
    within = np.arange(int(sizes.sum()), dtype=np.int64) - np.repeat(
        np.cumsum(sizes) - sizes, sizes
    )
    px = lo[index, 0] + within % span[index, 0]
    py = lo[index, 1] + within // span[index, 0]

    a, b, c = tri_xy[index, 0], tri_xy[index, 1], tri_xy[index, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    safe = np.where(np.abs(area) < 1e-12, 1e-12, area)
    fx, fy = px.astype(np.float32), py.astype(np.float32)
    w0 = ((b[:, 0] - fx) * (c[:, 1] - fy) - (b[:, 1] - fy) * (c[:, 0] - fx)) / safe
    w1 = ((c[:, 0] - fx) * (a[:, 1] - fy) - (c[:, 1] - fy) * (a[:, 0] - fx)) / safe
    w2 = 1.0 - w0 - w1
    inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (np.abs(area) > 1e-12)
    if not inside.any():
        return

    index, px, py = index[inside], px[inside], py[inside]
    zs = (
        w0[inside] * tri_depth[index, 0]
        + w1[inside] * tri_depth[index, 1]
        + w2[inside] * tri_depth[index, 2]
    )
    flat = py * width + px
    ordered = np.argsort(zs, kind="stable")
    pixels, first = np.unique(flat[ordered], return_index=True)
    winners = ordered[first]
    nearer = zs[winners] < zbuffer[pixels]
    pixels, winners = pixels[nearer], winners[nearer]
    zbuffer[pixels] = zs[winners]
    canvas[pixels] = colour[index[winners]]


def _draw_overlay(canvas, segments, camera: Camera, style: Style, width: int, height: int) -> None:
    """Draw line segments over an already-shaded canvas, without a depth test.

    Feature lines are what make a shaded part readable; hiding the ones behind
    the surface needs a depth buffer this renderer does not keep, so they are
    drawn faintly rather than not at all.
    """
    flat_xy, flat_depth = camera.project(np.asarray(segments, dtype=float).reshape(-1, 3))
    xy = flat_xy.reshape(-1, 2, 2)
    depth = flat_depth.reshape(-1, 2)
    in_front = (depth > 0).all(axis=1) if camera.perspective else np.ones(len(xy), bool)
    lo, hi = xy.min(axis=1), xy.max(axis=1)
    on_screen = (hi[:, 0] >= 0) & (lo[:, 0] < width) & (hi[:, 1] >= 0) & (lo[:, 1] < height)
    keep = in_front & on_screen
    if not keep.any():
        return
    points, depths = _sample_segments(xy, depth, keep)
    px = np.rint(points[:, 0]).astype(np.int64)
    py = np.rint(points[:, 1]).astype(np.int64)
    ok = (px >= 0) & (px < width) & (py >= 0) & (py < height)
    px, py, depths = px[ok], py[ok], depths[ok]
    if not len(px):
        return
    near, far = float(depths.min()), float(depths.max())
    span = far - near
    fade = (depths - near) / span if span > 1e-12 else np.zeros_like(depths)
    flat = py * width + px
    blend = (0.25 + 0.55 * (1.0 - fade))[:, None]
    canvas[flat] = canvas[flat] * (1.0 - blend) + np.array(style.overlay, np.float32) * blend
