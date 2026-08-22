"""ezf3d — read Autodesk Fusion 360 designs without Fusion.

>>> import ezf3d
>>> doc = ezf3d.readfile("Design.f3d")
>>> doc.manifest.doc_type
'Fusion Document'
>>> doc.bodies[0].census().faces
446
"""

from ezf3d.model.document import (
    DESIGN_SUFFIX,
    PACKAGE_SUFFIX,
    Asset,
    Body,
    Document,
    Ef3dError,
    readfile,
)

__version__ = "0.1.0"

__all__ = [
    "DESIGN_SUFFIX",
    "PACKAGE_SUFFIX",
    "Asset",
    "Body",
    "Document",
    "Ef3dError",
    "__version__",
    "readfile",
]
