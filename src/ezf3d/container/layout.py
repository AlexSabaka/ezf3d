"""Map an archive's entry names onto Fusion's document layout.

A Fusion document is a tree of *asset folders* (``FusionAssetName[Active]``,
``Animation``, ...), each holding *segments* — a ``MetaStream.dat`` /
``BulkStream.dat`` pair — plus blob folders for B-Rep bodies, materials,
images and the graphics cache.

Segment folder names drift between Fusion versions (``Design1`` in one file,
``FusionDesignSegmentType1`` in another), so nothing here hardcodes a name:
a segment is *any* directory containing both stream files.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from ezf3d.container.archive import F3DArchive

META_STREAM = "MetaStream.dat"
BULK_STREAM = "BulkStream.dat"

#: ``BREP.<uuid>.smb`` (plain body) or ``.smbh`` (body carrying ASM history).
BREP_RE = re.compile(r"^BREP\.(?P<uuid>[0-9a-fA-F-]{36})\.(?P<kind>smbh?)$")


@dataclass(slots=True)
class SegmentLocation:
    """Where a segment's two streams live inside the archive."""

    name: str
    meta_path: str
    bulk_path: str
    meta_size: int = 0
    bulk_size: int = 0


@dataclass(slots=True)
class BrepLocation:
    """One ``Breps.BlobParts`` entry."""

    uuid: str
    path: str
    size: int
    #: ``True`` for ``.smbh`` — an ASM body that carries rollback history.
    has_history: bool

    @property
    def suffix(self) -> str:
        return "smbh" if self.has_history else "smb"


@dataclass(slots=True)
class AssetFolderLayout:
    """One top-level asset folder, e.g. ``FusionAssetName[Active]``."""

    #: Folder name as it appears in the archive, brackets included.
    folder: str
    #: Folder name with the ``[State]`` suffix stripped.
    asset: str
    #: The ``[State]`` suffix, e.g. ``Active``.
    state: str | None
    segments: dict[str, SegmentLocation] = field(default_factory=dict)
    breps: list[BrepLocation] = field(default_factory=list)
    proteins: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    previews: list[str] = field(default_factory=list)
    #: ``OGS.BlobFolder/...`` entries — the pre-tessellated graphics cache.
    ogs: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    @property
    def has_graphics_cache(self) -> bool:
        return bool(self.ogs)


@dataclass(slots=True)
class DocumentLayout:
    """Everything an archive holds, sorted into its Fusion roles."""

    manifest_path: str | None = None
    properties_path: str | None = None
    component_reference_path: str | None = None
    assets: dict[str, AssetFolderLayout] = field(default_factory=dict)
    loose: list[str] = field(default_factory=list)

    @property
    def primary(self) -> AssetFolderLayout | None:
        """The ``[Active]`` asset folder, else the first one found."""
        for asset in self.assets.values():
            if asset.state == "Active":
                return asset
        return next(iter(self.assets.values()), None)


_STATE_RE = re.compile(r"^(?P<asset>.+?)\[(?P<state>[^\]]+)\]$")


def _split_state(folder: str) -> tuple[str, str | None]:
    match = _STATE_RE.match(folder)
    if match:
        return match["asset"], match["state"]
    return folder, None


def discover_layout(archive: F3DArchive) -> DocumentLayout:
    """Classify every entry of *archive* into a :class:`DocumentLayout`."""
    layout = DocumentLayout()
    sizes = {e.name: e.size for e in archive.entries()}

    # Pass 1: bucket entries by their top-level folder.
    by_folder: dict[str, list[str]] = {}
    for name in sizes:
        head, _, tail = name.partition("/")
        if not tail:
            if name == "Manifest.dat":
                layout.manifest_path = name
            elif name == "Properties.dat":
                layout.properties_path = name
            elif name == "ComponentReferenceData.json":
                layout.component_reference_path = name
            else:
                layout.loose.append(name)
            continue
        by_folder.setdefault(head, []).append(name)

    # Pass 2: classify each asset folder's contents.
    for folder, names in sorted(by_folder.items()):
        asset_name, state = _split_state(folder)
        asset = AssetFolderLayout(folder=folder, asset=asset_name, state=state)
        stream_dirs: dict[str, dict[str, str]] = {}

        for name in sorted(names):
            rel = name[len(folder) + 1 :]
            parent, _, base = rel.rpartition("/")
            top = parent.partition("/")[0]

            if base in (META_STREAM, BULK_STREAM):
                stream_dirs.setdefault(parent, {})[base] = name
            elif top == "Breps.BlobParts" and (m := BREP_RE.match(base)):
                asset.breps.append(
                    BrepLocation(
                        uuid=m["uuid"],
                        path=name,
                        size=sizes[name],
                        has_history=m["kind"] == "smbh",
                    )
                )
            elif top == "ProteinAssets.BlobParts":
                asset.proteins.append(name)
            elif top == "Images.BlobParts":
                asset.images.append(name)
            elif top == "Previews":
                asset.previews.append(name)
            elif top.startswith("OGS."):
                asset.ogs.append(name)
            elif base == "Manifest.dat" and not parent:
                asset.other.append(name)
            else:
                asset.other.append(name)

        for parent, pair in sorted(stream_dirs.items()):
            if META_STREAM not in pair or BULK_STREAM not in pair:
                # A half-populated segment is malformed; keep it visible rather
                # than dropping it silently.
                asset.other.extend(pair.values())
                continue
            asset.segments[posixpath.basename(parent) or parent] = SegmentLocation(
                name=posixpath.basename(parent) or parent,
                meta_path=pair[META_STREAM],
                bulk_path=pair[BULK_STREAM],
                meta_size=sizes[pair[META_STREAM]],
                bulk_size=sizes[pair[BULK_STREAM]],
            )

        asset.breps.sort(key=lambda b: b.path)
        layout.assets[folder] = asset

    return layout
