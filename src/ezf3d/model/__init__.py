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
from ezf3d.model.timeline import Feature, Timeline, read_timeline

__all__ = [
    "DESIGN_SUFFIX",
    "PACKAGE_SUFFIX",
    "Asset",
    "Body",
    "Component",
    "Design",
    "Document",
    "Ef3dError",
    "Feature",
    "Parameter",
    "Parameters",
    "Timeline",
    "read_design",
    "read_parameters",
    "read_timeline",
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
    TimelineEntry,
    TimelineInfo,
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
    "TimelineEntry",
    "TimelineInfo",
    "Totals",
]
