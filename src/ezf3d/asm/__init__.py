"""Autodesk Shape Manager (ASM) B-Rep reading.

ASM is a fork of ACIS 7.0, so the on-disk container is a SAB stream with
Autodesk's entity vocabulary and — in ``ASM BinaryFile8`` — 64-bit pointers.

Three layers, each usable on its own:

``header`` / ``tokens`` / ``records``
    The stream: signature, token grammar, and the entity graph.
``brep``
    Typed traversal of the ACIS hierarchy, resolving by base class.
``geometry``
    Analytic curves and surfaces, with evaluation and inversion.
"""

from ezf3d.asm.brep import Body, Coedge, Edge, Face, Loop, Lump, Node, Shape, Shell, Vertex
from ezf3d.asm.geometry import (
    ANALYTIC_CURVE_NAMES,
    ANALYTIC_SURFACE_NAMES,
    Cone,
    Curve,
    Ellipse,
    GeometryError,
    Plane,
    Sphere,
    SplineCurve,
    SplineSurface,
    Straight,
    Surface,
    Torus,
    read_curve,
    read_surface,
)
from ezf3d.asm.header import AsmError, AsmHeader, read_header
from ezf3d.asm.records import (
    END_MARKER,
    HISTORY_BEGIN_MARKER,
    HISTORY_END_MARKER,
    NULL,
    AsmModel,
    Entity,
    parse,
)
from ezf3d.asm.tokens import Record, Tag, TokenError, tokenize
from ezf3d.asm.topology import (
    ANALYTIC_CURVES,
    ANALYTIC_SURFACES,
    KERNEL_UNIT,
    Bounds,
    TopologyCensus,
    census,
)

__all__ = [
    "ANALYTIC_CURVES",
    "ANALYTIC_CURVE_NAMES",
    "ANALYTIC_SURFACES",
    "ANALYTIC_SURFACE_NAMES",
    "END_MARKER",
    "HISTORY_BEGIN_MARKER",
    "HISTORY_END_MARKER",
    "KERNEL_UNIT",
    "NULL",
    "AsmError",
    "AsmHeader",
    "AsmModel",
    "Body",
    "Bounds",
    "Coedge",
    "Cone",
    "Curve",
    "Edge",
    "Ellipse",
    "Entity",
    "Face",
    "GeometryError",
    "Loop",
    "Lump",
    "Node",
    "Plane",
    "Record",
    "Shape",
    "Shell",
    "Sphere",
    "SplineCurve",
    "SplineSurface",
    "Straight",
    "Surface",
    "Tag",
    "TokenError",
    "TopologyCensus",
    "Torus",
    "Vertex",
    "census",
    "parse",
    "read_curve",
    "read_header",
    "read_surface",
    "tokenize",
]
