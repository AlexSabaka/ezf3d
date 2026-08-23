"""Document model."""

from ezf3d.model.design import Component, Design, read_design
from ezf3d.model.document import (
    DESIGN_SUFFIX,
    PACKAGE_SUFFIX,
    Asset,
    Body,
    Document,
    Ef3dError,
    readfile,
)

__all__ = [
    "DESIGN_SUFFIX",
    "PACKAGE_SUFFIX",
    "Asset",
    "Body",
    "Component",
    "Design",
    "Document",
    "Ef3dError",
    "read_design",
    "readfile",
]

from ezf3d.model.report import (
    AssetInfo,
    BodyInfo,
    BoundsInfo,
    DocumentInfo,
    Envelope,
    KernelInfo,
    PackageInfo,
    PackageMember,
    SegmentInfo,
    Totals,
)

__all__ += [
    "AssetInfo",
    "BodyInfo",
    "BoundsInfo",
    "DocumentInfo",
    "Envelope",
    "KernelInfo",
    "PackageInfo",
    "PackageMember",
    "SegmentInfo",
    "Totals",
]
