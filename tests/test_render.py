"""Camera, rasteriser and PNG output.

The check that matters is that the picture agrees with the geometry it claims
to show: the ink in the image must land where the camera says the model
projects to.  A renderer that silently drew nothing, or drew the wrong part of
the model, would pass every other test here.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

import ezf3d
from ezf3d.render import (
    VIEW_DIRECTIONS,
    Camera,
    Style,
    build_scene,
    contact_sheet,
    encode,
    ink_bounds,
    render_lines,
    write,
)


def decode_png(data: bytes) -> np.ndarray:
    """Minimal PNG reader, so the encoder is checked against something else."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    header = None
    payload = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, f"bad CRC on {kind!r}"
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            payload += body
        pos += 12 + length
    assert header is not None
    width, height, depth, colour, _, _, _ = header
    assert depth == 8 and colour in (2, 6)
    channels = 3 if colour == 2 else 4
    raw = zlib.decompress(payload)
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, width * channels + 1)
    assert (rows[:, 0] == 0).all(), "only filter type 0 is written"
    return rows[:, 1:].reshape(height, width, channels)


# -- png -------------------------------------------------------------------


def test_png_round_trips():
    rng = np.random.default_rng(0)
    pixels = rng.integers(0, 256, size=(17, 23, 3), dtype=np.uint8)
    assert np.array_equal(decode_png(encode(pixels)), pixels)


def test_png_rejects_bad_shapes():
    with pytest.raises(ValueError):
        encode(np.zeros((4, 4), dtype=np.uint8))
    with pytest.raises(ValueError):
        encode(np.zeros((4, 4, 2), dtype=np.uint8))


# -- camera ----------------------------------------------------------------


def test_named_views_face_the_model():
    lower, upper = np.zeros(3), np.array([10.0, 20.0, 5.0])
    centre = (lower + upper) / 2.0
    for view in VIEW_DIRECTIONS:
        camera = Camera.fit(lower, upper, view=view, width=200, height=100)
        xy, depth = camera.project(centre[None, :])
        assert xy[0] == pytest.approx([100.0, 50.0], abs=1e-6), view
        assert depth[0] > 0, view


def test_orthographic_projection_is_linear_in_depth():
    camera = Camera.fit(np.zeros(3), np.ones(3) * 10, view="front", width=200, height=200)
    near = np.array([[5.0, 0.0, 5.0]])
    far = np.array([[5.0, 10.0, 5.0]])
    (xy_near, d_near), (xy_far, d_far) = camera.project(near), camera.project(far)
    # Orthographic: moving along the view axis changes depth, never position.
    assert xy_near[0] == pytest.approx(xy_far[0], abs=1e-9)
    assert d_far[0] > d_near[0]


def test_perspective_shrinks_with_distance():
    camera = Camera.fit(
        np.zeros(3), np.ones(3) * 10, view="front", width=200, height=200, perspective=True
    )
    near = camera.project(np.array([[7.0, 0.0, 5.0]]))[0]
    far = camera.project(np.array([[7.0, 10.0, 5.0]]))[0]
    assert abs(near[0][0] - 100.0) > abs(far[0][0] - 100.0)


def test_orbit_keeps_the_target_and_distance():
    camera = Camera.fit(np.zeros(3), np.ones(3) * 4, view="iso", width=100, height=100)
    turned = camera.orbit(math.pi / 3)
    assert np.allclose(turned.target, camera.target)
    assert float(np.linalg.norm(turned.eye - turned.target)) == pytest.approx(
        float(np.linalg.norm(camera.eye - camera.target))
    )
    assert not np.allclose(turned.eye, camera.eye)


def test_unknown_view_is_rejected():
    with pytest.raises(ValueError, match="unknown view"):
        Camera.fit(np.zeros(3), np.ones(3), view="sideways")


# -- rasteriser ------------------------------------------------------------


def test_a_known_square_lands_where_the_camera_says():
    corners = np.array([[-1.0, 0.0, -1.0], [1.0, 0.0, -1.0], [1.0, 0.0, 1.0], [-1.0, 0.0, 1.0]])
    segments = np.stack([corners, np.roll(corners, -1, axis=0)], axis=1)
    camera = Camera.fit_points(corners, view="front", width=200, height=200, margin=1.0)
    image = render_lines(segments, camera)
    assert image.shape == (200, 200, 3)
    ink = ink_bounds(image, Style().background)
    xy, _ = camera.project(corners)
    expected = (xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max())
    for got, want in zip(ink, expected, strict=True):
        assert abs(got - want) <= 2, f"{ink} vs {expected}"


def test_empty_input_gives_a_blank_frame():
    camera = Camera.fit(np.zeros(3), np.ones(3), width=32, height=24)
    image = render_lines(np.zeros((0, 2, 3)), camera)
    assert image.shape == (24, 32, 3)
    assert ink_bounds(image, Style().background) is None


def test_rendering_is_deterministic(wheel):
    with ezf3d.readfile(wheel) as doc:
        scene = build_scene(doc, body=doc.bodies[0].uuid[:8])
        camera = Camera.fit_points(scene.points(), view="iso", width=200, height=150)
        first = render_lines(scene.segments, camera)
        second = render_lines(scene.segments, camera)
    assert np.array_equal(first, second)


def test_contact_sheet_tiles_without_losing_a_frame():
    tiles = [np.full((10, 12, 3), value, dtype=np.uint8) for value in (10, 20, 30, 40, 50)]
    sheet = contact_sheet(tiles, columns=3)
    assert sheet.shape == (20, 36, 3)
    assert sheet[0, 0, 0] == 10 and sheet[0, 12, 0] == 20 and sheet[10, 0, 0] == 40


# -- end to end ------------------------------------------------------------


def test_render_agrees_with_the_projected_geometry(bhujha, tmp_path: Path):
    """The plan's acceptance check: the ink must match the projection.

    A render that quietly dropped geometry, or framed the wrong thing, would
    still produce a plausible picture — this is what catches that.
    """
    out = tmp_path / "rb.png"
    with ezf3d.readfile(bhujha) as doc:
        scene = build_scene(doc)
        assert not scene.is_empty
        camera = Camera.fit_points(scene.points(), view="iso", width=640, height=480)
        image = render_lines(scene.segments, camera)
        written = write(out, image)

    assert written == out.stat().st_size
    assert np.array_equal(decode_png(out.read_bytes()), image)

    xy, _ = camera.project(scene.points())
    inside = (
        (xy[:, 0] >= 0) & (xy[:, 0] < camera.width) & (xy[:, 1] >= 0) & (xy[:, 1] < camera.height)
    )
    visible = xy[inside]
    expected = (
        visible[:, 0].min(),
        visible[:, 1].min(),
        visible[:, 0].max(),
        visible[:, 1].max(),
    )
    ink = ink_bounds(image, Style().background)
    assert ink is not None
    for got, want in zip(ink, expected, strict=True):
        assert abs(got - want) <= 2, f"ink {ink} vs projected {expected}"

    # The frame is actually used rather than the model sitting in a corner.
    coverage = (ink[2] - ink[0]) / camera.width
    assert coverage > 0.8, f"model covers only {coverage:.0%} of the frame"


def test_single_body_render_is_smaller_than_the_whole_document(bhujha, tmp_path: Path):
    with ezf3d.readfile(bhujha) as doc:
        whole = build_scene(doc)
        one = build_scene(doc, body=doc.bodies[0].uuid[:8])
    assert one.bodies == 1
    assert len(one.segments) < len(whole.segments)


def test_unknown_body_is_rejected(wheel):
    with ezf3d.readfile(wheel) as doc, pytest.raises(KeyError):
        build_scene(doc, body="nosuchbody")
