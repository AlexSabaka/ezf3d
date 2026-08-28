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
from ezf3d.model.materials import Assignment, Materials, read_assignments, read_catalogue
from ezf3d.model.parameters import Parameter, Parameters, read_parameters
from ezf3d.model.sketch import Curve, Point, Sketch, Sketches, read_sketches
from ezf3d.model.timeline import Extrude, Feature, Timeline, read_extrude, read_timeline

__all__ = [
    "DESIGN_SUFFIX",
    "PACKAGE_SUFFIX",
    "Asset",
    "Assignment",
    "Body",
    "Component",
    "Curve",
    "Design",
    "Document",
    "Ef3dError",
    "Extrude",
    "Feature",
    "Materials",
    "Parameter",
    "Parameters",
    "Point",
    "Sketch",
    "Sketches",
    "Timeline",
    "read_assignments",
    "read_catalogue",
    "read_design",
    "read_extrude",
    "read_parameters",
    "read_sketches",
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
    SketchesInfo,
    SketchInfo,
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
    "SketchInfo",
    "SketchesInfo",
    "TimelineEntry",
    "TimelineInfo",
    "Totals",
]
