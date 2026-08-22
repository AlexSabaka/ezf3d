"""Mesh writers.

Each format is read back by an independent parser written for the test, so a
writer cannot pass by agreeing with itself.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

import ezf3d
from ezf3d.asm.brep import Shape
from ezf3d.export import CM_TO_MM, FORMATS, ExportError, write_mesh
from ezf3d.mesh import Mesh, tessellate


@pytest.fixture(scope="module")
def part(request):
    """One tessellated body, shared by the writers under test."""
    path = Path(__file__).resolve().parent.parent / "data" / "Robotic_Bhujha.f3d"
    if not path.exists():
        pytest.skip("sample not available")
    with ezf3d.readfile(path) as doc:
        body = next(b for b in doc.bodies if b.uuid.startswith("2f2ab6d9"))
        return tessellate(Shape(body.model()), measure=False).mesh


def read_binary_stl(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    (count,) = struct.unpack("<I", raw[80:84])
    assert len(raw) == 84 + 50 * count, "declared facet count disagrees with the size"
    corners = np.zeros((count, 3, 3))
    for index in range(count):
        block = raw[84 + 50 * index : 84 + 50 * index + 48]
        values = struct.unpack("<12f", block)
        corners[index] = np.array(values[3:]).reshape(3, 3)
    return corners


def read_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    vertices, faces = [], []
    for line in path.read_text().splitlines():
        if line.startswith("v "):
            vertices.append([float(v) for v in line.split()[1:4]])
        elif line.startswith("f "):
            faces.append([int(v.split("/")[0]) - 1 for v in line.split()[1:4]])
    return np.array(vertices), np.array(faces)


def test_stl_round_trips(part, tmp_path: Path):
    out = tmp_path / "part.stl"
    written = write_mesh(part, out, "stl")
    assert written == out.stat().st_size
    corners = read_binary_stl(out)
    assert len(corners) == len(part)
    # Written in millimetres, so the bounds are ten times the kernel's.
    lower, upper = part.bounds()
    assert np.allclose(corners.reshape(-1, 3).min(axis=0), lower * CM_TO_MM, atol=1e-3)
    assert np.allclose(corners.reshape(-1, 3).max(axis=0), upper * CM_TO_MM, atol=1e-3)


def test_ascii_stl_declares_the_same_facets(part, tmp_path: Path):
    out = tmp_path / "part.stl"
    write_mesh(part, out, "stl-ascii")
    text = out.read_text()
    assert text.startswith("solid ")
    assert text.rstrip().endswith("endsolid ezf3d")
    assert text.count("facet normal") == len(part)
    assert text.count("vertex") == 3 * len(part)


def test_obj_round_trips(part, tmp_path: Path):
    out = tmp_path / "part.obj"
    write_mesh(part, out, "obj")
    vertices, faces = read_obj(out)
    assert len(vertices) == len(part.vertices)
    assert len(faces) == len(part)
    assert faces.min() >= 0 and faces.max() < len(vertices)
    assert np.allclose(vertices, part.vertices * CM_TO_MM, atol=1e-5)


def test_gltf_is_valid_and_self_contained(part, tmp_path: Path):
    out = tmp_path / "part.gltf"
    write_mesh(part, out, "gltf")
    document = json.loads(out.read_text())
    assert document["asset"]["version"] == "2.0"
    position, indices = document["accessors"]
    assert position["count"] == len(part.vertices)
    assert indices["count"] == 3 * len(part)
    assert document["buffers"][0]["uri"].startswith("data:")
    lower, _upper = part.bounds()
    assert np.allclose(position["min"], lower * CM_TO_MM, atol=1e-3)


def test_glb_chunks_are_well_formed(part, tmp_path: Path):
    out = tmp_path / "part.glb"
    write_mesh(part, out, "glb")
    raw = out.read_bytes()
    magic, version, length = struct.unpack("<III", raw[:12])
    assert magic == 0x46546C67 and version == 2
    assert length == len(raw), "header length must match the file"
    json_length, json_type = struct.unpack("<II", raw[12:20])
    assert json_type == 0x4E4F534A
    document = json.loads(raw[20 : 20 + json_length])
    assert document["meshes"][0]["primitives"][0]["indices"] == 1
    # Every chunk starts on a four-byte boundary.
    assert json_length % 4 == 0


def test_unit_option_scales(part, tmp_path: Path):
    millimetres = tmp_path / "mm.obj"
    centimetres = tmp_path / "cm.obj"
    write_mesh(part, millimetres, "obj", scale=CM_TO_MM)
    write_mesh(part, centimetres, "obj", scale=1.0)
    mm_vertices, _ = read_obj(millimetres)
    cm_vertices, _ = read_obj(centimetres)
    assert np.allclose(mm_vertices, cm_vertices * CM_TO_MM, atol=1e-4)


def test_every_advertised_format_writes(part, tmp_path: Path):
    for fmt in FORMATS:
        out = tmp_path / f"part.{fmt}"
        assert write_mesh(part, out, fmt) == out.stat().st_size


def test_bad_input_is_rejected(part, tmp_path: Path):
    with pytest.raises(ExportError, match="unknown format"):
        write_mesh(part, tmp_path / "x.step", "step")
    with pytest.raises(ExportError, match="no triangles"):
        write_mesh(Mesh(), tmp_path / "x.stl", "stl")
