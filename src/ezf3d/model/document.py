"""The unified document model — what :func:`ezf3d.readfile` hands back.

Loading is lazy on purpose.  A single body can be 25 MB of ASM and a document
can hold twenty of them, so nothing heavier than the manifests is touched until
something asks for it.  ``ezf3d info`` therefore costs a few kilobytes of I/O
regardless of design size.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from ezf3d.asm.records import AsmModel
from ezf3d.asm.records import parse as parse_asm
from ezf3d.asm.topology import TopologyCensus, census
from ezf3d.container.archive import F3DArchive
from ezf3d.container.layout import AssetFolderLayout, BrepLocation, DocumentLayout, discover_layout
from ezf3d.container.package import PackageEntry, PackageIndex, read_package_index
from ezf3d.model.materials import read_catalogue
from ezf3d.ogs.cache import GraphicsCache, read_cache
from ezf3d.streams.manifest import (
    AssetManifest,
    DocumentManifest,
    read_asset_manifest,
    read_document_manifest,
)
from ezf3d.streams.segment import Segment, read_segment

#: Fusion's own extensions.
DESIGN_SUFFIX = ".f3d"
PACKAGE_SUFFIX = ".f3z"


class Ef3dError(Exception):
    """Base class for ezf3d failures."""


@dataclass(slots=True)
class Body:
    """One ASM B-Rep body blob, loaded on demand."""

    uuid: str
    path: str
    size: int
    has_history: bool
    _archive: F3DArchive
    _model: AsmModel | None = field(default=None, repr=False)
    _census: TopologyCensus | None = field(default=None, repr=False)

    @property
    def suffix(self) -> str:
        return "smbh" if self.has_history else "smb"

    def raw(self) -> bytes:
        return self._archive.read(self.path)

    def model(self) -> AsmModel:
        """Parse the body, caching the result."""
        if self._model is None:
            self._model = parse_asm(self.raw())
        return self._model

    def census(self) -> TopologyCensus:
        """Topology and geometry census, caching the result."""
        if self._census is None:
            self._census = census(self.model())
        return self._census


@dataclass(slots=True)
class Asset:
    """One asset folder — a design, an animation, a simulation."""

    folder: str
    name: str
    state: str | None
    manifest: AssetManifest
    layout: AssetFolderLayout
    bodies: list[Body]
    _archive: F3DArchive
    _segments: dict[str, Segment] | None = field(default=None, repr=False)
    _cache: GraphicsCache | None = field(default=None, repr=False)
    _catalogue: dict[str, str] | None = field(default=None, repr=False)

    @property
    def segments(self) -> dict[str, Segment]:
        """All segments, parsed on first access."""
        if self._segments is None:
            declarations = self.manifest.segments
            parsed: dict[str, Segment] = {}
            for name, loc in self.layout.segments.items():
                declared = next((d for d in declarations if d.matches(name)), None)
                parsed[name] = read_segment(
                    name,
                    self._archive.read(loc.meta_path),
                    self._archive.read(loc.bulk_path),
                    segment_type=declared.segment_type if declared else "",
                )
                parsed[name].meta_size = loc.meta_size
                parsed[name].bulk_size = loc.bulk_size
            self._segments = parsed
        return self._segments

    @property
    def design(self) -> Segment | None:
        """The design segment — the parametric timeline."""
        return next((s for s in self.segments.values() if s.is_design), None)

    @property
    def has_graphics_cache(self) -> bool:
        """True when Fusion left its tessellated display mesh in the file."""
        return self.layout.has_graphics_cache

    def graphics_cache(self) -> GraphicsCache | None:
        """Fusion's cached tessellation, or ``None`` if this asset has none."""
        if self._cache is None:
            if not self.layout.ogs:
                return None
            self._cache = read_cache(self._archive.read, self.layout.ogs)
        return self._cache

    def preview(self) -> bytes | None:
        """The embedded thumbnail PNG, if this asset has one."""
        if not self.layout.previews:
            return None
        return self._archive.read(self.layout.previews[0])

    def raw(self, path: str) -> bytes:
        """One of this asset's archive entries, decompressed."""
        return self._archive.read(path)

    def protein_catalogue(self) -> dict[str, str]:
        """``asset id -> category`` for every material this asset packages.

        Reads only the packages' tables of contents, which is enough to say
        what kind of asset a design's assignment names.  Cached, because the
        blobs decompress to tens of kilobytes and several callers want it.
        """
        if self._catalogue is None:
            catalogue: dict[str, str] = {}
            for path in self.layout.proteins:
                catalogue.update(read_catalogue(self._archive.read(path)))
            self._catalogue = catalogue
        return self._catalogue


@dataclass(slots=True)
class Document:
    """A Fusion design — one ``.f3d``, or the root of a ``.f3z``."""

    source: str
    manifest: DocumentManifest
    layout: DocumentLayout
    assets: dict[str, Asset]
    archive: F3DArchive
    #: Present only when this document came from a ``.f3z``.
    package: PackageIndex | None = None
    #: Sibling documents of a ``.f3z``, keyed by their archive path.
    linked: dict[str, Document] = field(default_factory=dict)
    #: Friendly name from the package index, when there is one.
    package_name: str | None = None
    _owns_archive: bool = True

    # -- convenience -------------------------------------------------------

    @property
    def name(self) -> str:
        if self.package_name:
            return self.package_name
        return Path(self.source).stem

    @property
    def primary(self) -> Asset | None:
        """The ``[Active]`` asset — where the design lives."""
        for asset in self.assets.values():
            if asset.state == "Active":
                return asset
        return next(iter(self.assets.values()), None)

    @property
    def bodies(self) -> list[Body]:
        """Every B-Rep body across every asset."""
        return [b for asset in self.assets.values() for b in asset.bodies]

    @property
    def segments(self) -> dict[str, Segment]:
        """Segments of the primary asset."""
        return self.primary.segments if self.primary else {}

    @property
    def design(self) -> Segment | None:
        return self.primary.design if self.primary else None

    @property
    def is_package(self) -> bool:
        return self.package is not None

    def documents(self) -> list[Document]:
        """This document followed by every linked one, depth-first."""
        result = [self]
        for child in self.linked.values():
            result.extend(child.documents())
        return result

    def preview(self) -> bytes | None:
        for asset in self.assets.values():
            data = asset.preview()
            if data:
                return data
        return None

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        for child in self.linked.values():
            child.close()
        if self._owns_archive:
            self.archive.close()

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _build_assets(archive: F3DArchive, layout: DocumentLayout) -> dict[str, Asset]:
    assets: dict[str, Asset] = {}
    for folder, asset_layout in layout.assets.items():
        manifest_path = f"{folder}/Manifest.dat"
        if manifest_path not in archive:
            continue
        manifest = read_asset_manifest(archive.read(manifest_path))
        assets[folder] = Asset(
            folder=folder,
            name=manifest.asset_name or asset_layout.asset,
            state=asset_layout.state,
            manifest=manifest,
            layout=asset_layout,
            bodies=[_body(archive, loc) for loc in asset_layout.breps],
            _archive=archive,
        )
    return assets


def _body(archive: F3DArchive, loc: BrepLocation) -> Body:
    return Body(
        uuid=loc.uuid,
        path=loc.path,
        size=loc.size,
        has_history=loc.has_history,
        _archive=archive,
    )


def _open_document(
    archive: F3DArchive,
    source: str,
    *,
    owns_archive: bool = True,
    package_name: str | None = None,
) -> Document:
    layout = discover_layout(archive)
    if layout.manifest_path is None:
        raise Ef3dError(f"{source}: no Manifest.dat — not a Fusion document")
    manifest = read_document_manifest(archive.read(layout.manifest_path))
    return Document(
        source=source,
        manifest=manifest,
        layout=layout,
        assets=_build_assets(archive, layout),
        archive=archive,
        package_name=package_name,
        _owns_archive=owns_archive,
    )


def _open_package(archive: F3DArchive, source: str, index: PackageIndex) -> Document:
    """Open every ``.f3d`` inside a ``.f3z`` and link them by XREF."""
    opened: dict[str, Document] = {}
    for entry in index.entries.values():
        if entry.path and entry.path in archive:
            opened[entry.path] = _read_member(archive, source, entry)
    # Fall back to whatever .f3d entries exist when the sidecar is incomplete.
    for name in archive.namelist():
        if name.endswith(DESIGN_SUFFIX) and name not in opened:
            opened[name] = _read_member(archive, source, PackageEntry(0, Path(name).stem, name))

    root_path = index.root_path if index.root_path in opened else next(iter(opened), None)
    if root_path is None:
        raise Ef3dError(f"{source}: package contains no readable {DESIGN_SUFFIX} member")

    root = opened[root_path]
    root.package = index
    root_entry = index.root
    if root_entry is not None:
        for child in index.children(root_entry):
            if child.path in opened and child.path != root_path:
                root.linked[child.path] = opened[child.path]
    for path, doc in opened.items():
        if path != root_path and path not in root.linked:
            root.linked[path] = doc
    root._owns_archive = True
    return root


def _read_member(archive: F3DArchive, source: str, entry: PackageEntry) -> Document:
    inner = F3DArchive(io.BytesIO(archive.read(entry.path)))
    return _open_document(
        inner,
        f"{source}!{entry.path}",
        owns_archive=True,
        package_name=entry.name,
    )


def readfile(path: str | Path | IO[bytes]) -> Document:
    """Open a Fusion ``.f3d`` or ``.f3z`` and return its :class:`Document`.

    For a ``.f3z`` the root design is returned, with the other members reachable
    through :attr:`Document.linked` and the reference graph through
    :attr:`Document.package`.
    """
    source = str(path) if isinstance(path, (str, Path)) else getattr(path, "name", "<stream>")
    archive = F3DArchive(path)
    try:
        index = read_package_index(archive)
        if index is not None:
            return _open_package(archive, source, index)
        return _open_document(archive, source)
    except Exception:
        archive.close()
        raise
