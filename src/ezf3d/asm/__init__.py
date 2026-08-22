"""Autodesk Shape Manager (ASM) B-Rep reading.

ASM is a fork of ACIS 7.0, so the on-disk container is a SAB stream with
Autodesk's entity vocabulary and — in ``ASM BinaryFile8`` — 64-bit pointers.
"""

from ezf3d.asm.header import AsmError, AsmHeader, read_header
from ezf3d.asm.records import NULL, AsmModel, Entity, parse
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
    "ANALYTIC_SURFACES",
    "KERNEL_UNIT",
    "NULL",
    "AsmError",
    "AsmHeader",
    "AsmModel",
    "Bounds",
    "Entity",
    "Record",
    "Tag",
    "TokenError",
    "TopologyCensus",
    "census",
    "parse",
    "read_header",
    "tokenize",
]
