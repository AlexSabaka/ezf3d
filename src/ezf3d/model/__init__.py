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
from ezf3d.model.parameters import Parameter, Parameters, read_parameters

__all__ = [
    "DESIGN_SUFFIX",
    "PACKAGE_SUFFIX",
    "Asset",
    "Body",
    "Component",
    "Design",
    "Document",
    "Ef3dError",
    "Parameter",
    "Parameters",
    "read_design",
    "read_parameters",
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
    ParameterInfo,
    ParametersInfo,
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
    "ParameterInfo",
    "ParametersInfo",
    "SegmentInfo",
    "Totals",
]
