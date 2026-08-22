"""``.f3z`` package handling — the multi-document wrapper.

An ``.f3z`` is a flat ZIP holding one ``.f3d`` per referenced design plus two
JSON sidecars:

``Manifest.json``
    ``{"root": "<uuid>.f3d"}`` — names the entry document.

``DesignDescription.json``
    Autodesk's *Design Description* graph: every document with its friendly
    name, cloud URN, and XREF relationships to the others.  This is how an
    assembly that spans several ``.f3d`` files is stitched back together.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ezf3d.container.archive import F3DArchive

MANIFEST_JSON = "Manifest.json"
DESIGN_DESCRIPTION_JSON = "DesignDescription.json"


@dataclass(slots=True)
class PackageEntry:
    """One document inside an ``.f3z``."""

    object_id: int
    name: str
    path: str
    version: int = 1
    urn: str | None = None
    lineage: str | None = None
    content_type: str = "f3d"
    created_at: str | None = None
    description: str | None = None
    #: object_ids of documents this one references as external references.
    xrefs: list[int] = field(default_factory=list)


@dataclass(slots=True)
class PackageIndex:
    """The document graph of an ``.f3z``."""

    root_path: str | None
    entries: dict[int, PackageEntry] = field(default_factory=dict)

    @property
    def root(self) -> PackageEntry | None:
        if self.root_path is None:
            return None
        for entry in self.entries.values():
            if entry.path == self.root_path:
                return entry
        return None

    def children(self, entry: PackageEntry) -> list[PackageEntry]:
        return [self.entries[i] for i in entry.xrefs if i in self.entries]


def read_package_index(archive: F3DArchive) -> PackageIndex | None:
    """Parse the ``.f3z`` sidecars, or return ``None`` for a plain ``.f3d``."""
    names = set(archive.namelist())
    if MANIFEST_JSON not in names and DESIGN_DESCRIPTION_JSON not in names:
        return None

    root_path: str | None = None
    if MANIFEST_JSON in names:
        root_path = json.loads(archive.read(MANIFEST_JSON)).get("root")

    index = PackageIndex(root_path=root_path)
    if DESIGN_DESCRIPTION_JSON not in names:
        return index

    doc = json.loads(archive.read(DESIGN_DESCRIPTION_JSON))
    for graph in doc.get("designDescription", {}).get("designGraphs", []):
        for obj in graph.get("designObjects", []):
            meta = obj.get("metadata") or {}
            entry = PackageEntry(
                object_id=obj["id"],
                name=obj.get("friendlyName") or obj.get("displayName") or str(obj["id"]),
                path=obj.get("relativePath") or obj.get("downloadAs") or "",
                version=obj.get("version", 1),
                urn=obj.get("about"),
                lineage=obj.get("lineage"),
                content_type=obj.get("contentType", "f3d"),
                created_at=obj.get("createdAt"),
                description=meta.get("description"),
            )
            for ref in obj.get("references") or []:
                entry.xrefs.extend(ref.get("ids") or [])
            index.entries[entry.object_id] = entry
    return index
