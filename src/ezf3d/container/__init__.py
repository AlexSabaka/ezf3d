"""Archive-level access to Fusion 360 documents."""

from ezf3d.container.archive import F3DArchive, UnsupportedCompressionError
from ezf3d.container.layout import AssetFolderLayout, DocumentLayout, discover_layout
from ezf3d.container.package import PackageEntry, PackageIndex, read_package_index

__all__ = [
    "AssetFolderLayout",
    "DocumentLayout",
    "F3DArchive",
    "PackageEntry",
    "PackageIndex",
    "UnsupportedCompressionError",
    "discover_layout",
    "read_package_index",
]
