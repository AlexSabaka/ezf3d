"""Mesh writers.

Three formats, chosen for what they are good at: STL because every slicer and
mesh tool reads it, OBJ because it is legible in a text editor, and glTF
because it is what a viewer wants.  All are written from the same
:class:`~ezf3d.mesh.mesh.Mesh` and all emit millimetres, which is what the rest
of the world expects even though Fusion's kernel works in centimetres.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import numpy as np

from ezf3d.mesh.mesh import Mesh

#: Fusion's kernel unit is the centimetre; mesh formats conventionally use mm.
CM_TO_MM = 10.0

FORMATS = ("stl", "stl-ascii", "obj", "gltf", "glb")


class ExportError(ValueError):
    """Raised for an unsupported format or an empty mesh."""


def _scaled(mesh: Mesh, scale: float) -> np.ndarray:
    return mesh.vertices * scale


def write_stl(mesh: Mesh, path: Path, *, scale: float = CM_TO_MM, name: str = "ezf3d") -> int:
    """Binary STL.  Normals come from the winding."""
    corners = _scaled(mesh, scale)[mesh.triangles]
    normals = mesh.face_normals()
    header = name.encode("ascii", "replace")[:80].ljust(80, b"\0")
    body = bytearray(header)
    body += struct.pack("<I", len(mesh.triangles))
    block = np.zeros((len(mesh.triangles), 12), dtype="<f4")
    block[:, 0:3] = normals
    block[:, 3:12] = corners.reshape(-1, 9)
    payload = block.tobytes()
    # Each facet is 50 bytes: 12 floats plus a two-byte attribute count.
    for index in range(len(mesh.triangles)):
        body += payload[index * 48 : (index + 1) * 48]
        body += b"\0\0"
    path.write_bytes(bytes(body))
    return len(body)


def write_stl_ascii(mesh: Mesh, path: Path, *, scale: float = CM_TO_MM, name: str = "ezf3d") -> int:
    corners = _scaled(mesh, scale)[mesh.triangles]
    normals = mesh.face_normals()
    lines = [f"solid {name}"]
    for triangle, normal in zip(corners, normals, strict=True):
        lines.append(f"  facet normal {normal[0]:.6e} {normal[1]:.6e} {normal[2]:.6e}")
        lines.append("    outer loop")
        for vertex in triangle:
            lines.append(f"      vertex {vertex[0]:.6e} {vertex[1]:.6e} {vertex[2]:.6e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {name}\n")
    text = "\n".join(lines)
    path.write_text(text)
    return len(text)


def write_obj(mesh: Mesh, path: Path, *, scale: float = CM_TO_MM, name: str = "ezf3d") -> int:
    vertices = _scaled(mesh, scale)
    parts = [f"# written by ezf3d\no {name}\n"]
    parts.append("\n".join(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices) + "\n")
    # OBJ indices are 1-based.
    parts.append("\n".join(f"f {a + 1} {b + 1} {c + 1}" for a, b, c in mesh.triangles) + "\n")
    text = "".join(parts)
    path.write_text(text)
    return len(text)


def _gltf_document(mesh: Mesh, scale: float, name: str) -> tuple[dict, bytes]:
    vertices = _scaled(mesh, scale).astype("<f4")
    indices = mesh.triangles.astype("<u4")
    vertex_bytes = vertices.tobytes()
    index_bytes = indices.tobytes()
    # glTF requires each buffer view to start on a four-byte boundary.
    padding = (-len(vertex_bytes)) % 4
    buffer = vertex_bytes + b"\0" * padding + index_bytes
    lower, upper = vertices.min(axis=0), vertices.max(axis=0)

    document = {
        "asset": {"version": "2.0", "generator": "ezf3d"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,  # float
                "count": len(vertices),
                "type": "VEC3",
                "min": [float(v) for v in lower],
                "max": [float(v) for v in upper],
            },
            {
                "bufferView": 1,
                "componentType": 5125,  # unsigned int
                "count": int(indices.size),
                "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(vertex_bytes), "target": 34962},
            {
                "buffer": 0,
                "byteOffset": len(vertex_bytes) + padding,
                "byteLength": len(index_bytes),
                "target": 34963,
            },
        ],
        "buffers": [{"byteLength": len(buffer)}],
    }
    return document, buffer


def write_gltf(mesh: Mesh, path: Path, *, scale: float = CM_TO_MM, name: str = "ezf3d") -> int:
    """glTF 2.0 with the buffer inlined as a data URI, so it is one file."""
    document, buffer = _gltf_document(mesh, scale, name)
    document["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(
        buffer
    ).decode("ascii")
    text = json.dumps(document, separators=(",", ":"))
    path.write_text(text)
    return len(text)


def write_glb(mesh: Mesh, path: Path, *, scale: float = CM_TO_MM, name: str = "ezf3d") -> int:
    """Binary glTF — the same document with the buffer as a real chunk."""
    document, buffer = _gltf_document(mesh, scale, name)
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    buffer += b"\0" * ((-len(buffer)) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(buffer)
    blob = struct.pack("<III", 0x46546C67, 2, total)
    blob += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    blob += struct.pack("<II", len(buffer), 0x004E4942) + buffer
    path.write_bytes(blob)
    return len(blob)


_WRITERS = {
    "stl": write_stl,
    "stl-ascii": write_stl_ascii,
    "obj": write_obj,
    "gltf": write_gltf,
    "glb": write_glb,
}


def write_mesh(
    mesh: Mesh, path: Path, fmt: str, *, scale: float = CM_TO_MM, name: str = "ezf3d"
) -> int:
    """Write *mesh* in *fmt*; returns the number of bytes written."""
    if mesh.is_empty:
        raise ExportError("nothing to export: the mesh has no triangles")
    try:
        writer = _WRITERS[fmt]
    except KeyError:
        raise ExportError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}") from None
    return writer(mesh, Path(path), scale=scale, name=name)
