"""The design graph: roots, components, and which bodies belong to which.

The meta stream's index gives every object in the design segment an id, an
offset and an extent (see :mod:`ezf3d.streams.segment`).  This reads the
structure those objects form, without decoding the fields inside one.

Three things make that possible.

**The roots name themselves.**  An object listed in the meta stream's ``roots``
opens with its own type as a ``str8`` -- ``ComponentsRoot``, ``BodiesRoot``,
``SketchesRoot``, ``UnitSystems``.  Ordinary objects open with an empty string,
so a leading name is what marks a root.

**A reference is ``0x01`` then a ``u64`` object id.**  Read that way,
``ComponentsRoot`` yields exactly the components: one for the wheel and for
SUCKER, eleven for Robotic_Bhujha.  Scanning for the pattern is permissive
enough that following it transitively reaches everything from anywhere, so it
is used only where a record is known to be a list of one thing.

**Ids are issued in creation order, so a component owns a contiguous range.**
Everything between one component's id and the next belongs to it: its bodies,
its feature registry, its features.  The evidence is that the ranges come out
exactly right -- every component of every sample owns precisely two bodies, the
``.smbh`` with history and the ``.smb`` without, 22 of 22 for Robotic_Bhujha --
and that each range holds at most one feature registry, 11 for 11 components.

A component with no timeline of its own has no registry; two of the ``.f3z``
package's members are like that.
"""

from __future__ import annotations

import bisect
import re
import struct
from dataclasses import dataclass, field

from ezf3d.streams.primitives import scan_strings
from ezf3d.streams.segment import BulkObject, Segment, is_feature_type

#: How the design graph names a body: by its blob filename.
BREP_NAME_RE = re.compile(r"^BREP\.[0-9a-fA-F-]{36}\.smbh?$")

#: A reference: the byte ``0x01`` followed by a little-endian object id.
_REFERENCE = 0x01

#: Root object types seen so far, for reporting rather than for parsing.
KNOWN_ROOTS = (
    "AssetSettings",
    "BodiesRoot",
    "ComponentInstancesRoot",
    "ComponentsRoot",
    "NamedTrackedEntitySet",
    "OGSSerializer",
    "ProteinAssetManager",
    "SketchesRoot",
    "UnitSystems",
    "VisualAnalyses",
    "WorkingModelPlaceholderRoot",
    "rootInstance",
)


@dataclass(frozen=True, slots=True)
class Component:
    """One component of a design, and the id range it owns."""

    oid: int
    #: The last wide string before the record's trailing revision.  Fusion
    #: writes a GUID here for a component the user never named, and
    #: ``(Unsaved)`` for a design saved from an unsaved state.
    name: str
    #: One past the last id this component owns, or ``None`` for the last one.
    limit: int | None
    #: Blob filenames of the bodies in its range, in id order.
    bodies: tuple[str, ...] = ()
    #: Feature kinds its own registry declares.  Empty when it has none.
    features: frozenset[str] = frozenset()

    @property
    def is_named(self) -> bool:
        """False when Fusion stored a GUID rather than a name."""
        return not re.fullmatch(r"[0-9a-fA-F-]{36}", self.name)


@dataclass(slots=True)
class Design:
    """What a design segment says about itself."""

    #: Root type name to object id.
    roots: dict[str, int] = field(default_factory=dict)
    components: list[Component] = field(default_factory=list)
    #: Objects the segment indexes.
    objects: int = 0
    #: Body blob filenames found anywhere in the graph, in id order.
    bodies: tuple[str, ...] = ()

    @property
    def named_bodies(self) -> int:
        return sum(len(component.bodies) for component in self.components)

    def owner(self, oid: int) -> Component | None:
        """The component whose id range contains *oid*.

        The same rule the body mapping above is built on, exposed so anything
        else the graph indexes by id -- parameters, sketches -- can be
        attributed without repeating it.
        """
        ids = [component.oid for component in self.components]
        position = bisect.bisect_right(ids, oid) - 1
        return self.components[position] if position >= 0 else None


def _leading_name(body: bytes, item: BulkObject) -> str:
    """The record's own ``str8`` type, empty for everything but a root."""
    found = next(iter(scan_strings(body, start=item.offset, min_len=1)), None)
    return found.value if found is not None and found.offset == item.offset else ""


def _trailing_name(body: bytes, item: BulkObject) -> str:
    """The last wide string of a record, which is where a name is written."""
    wide = [
        found.value
        for found in scan_strings(body, start=item.offset, end=item.end, min_len=1)
        if found.kind == "wstr"
    ]
    return wide[-1] if wide else ""


def references(body: bytes, item: BulkObject, known: set[int]) -> list[int]:
    """Object ids *item* refers to, in the order they appear.

    Permissive by design: any ``0x01`` followed by eight bytes that spell a
    known id counts.  Good enough to read a list record, not good enough to
    walk the graph transitively -- doing that from any component of
    Robotic_Bhujha reaches all 22 bodies, because the false positives connect
    everything to everything.
    """
    raw = body[item.offset : item.end]
    out: list[int] = []
    seen: set[int] = set()
    for index in range(len(raw) - 8):
        if raw[index] != _REFERENCE:
            continue
        target = struct.unpack_from("<Q", raw, index + 1)[0]
        if target in known and target != item.oid and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def read_design(segment: Segment) -> Design:
    """Read a design segment's roots, components and body ownership."""
    body = segment.bulk.body
    items = segment.objects()
    design = Design(objects=len(items))
    if not items:
        return design

    by_id = {item.oid: item for item in items}
    for oid in segment.meta.roots:
        item = by_id.get(oid)
        if item is None:
            continue
        name = _leading_name(body, item)
        if name:
            design.roots[name] = oid

    root = design.roots.get("ComponentsRoot")
    if root is None:
        return design
    known = set(by_id)
    ids = sorted(oid for oid in references(body, by_id[root], known) if oid not in (0, root))
    if not ids:
        return design

    # Everything from one component's id up to the next belongs to it.
    bodies: dict[int, str] = {}
    registries: dict[int, frozenset[str]] = {}
    for item in items:
        names = [
            found.value for found in scan_strings(body, start=item.offset, end=item.end, min_len=4)
        ]
        blob = next((name for name in names if BREP_NAME_RE.match(name)), None)
        if blob is not None:
            bodies[item.oid] = blob
        kinds = {name for name in names if is_feature_type(name)}
        if len(kinds) >= 2:
            registries[item.oid] = frozenset(
                name.removeprefix("Dc").removesuffix("MetaType") for name in kinds
            )

    def owner(oid: int) -> int | None:
        position = bisect.bisect_right(ids, oid) - 1
        return ids[position] if position >= 0 else None

    owned: dict[int, list[str]] = {oid: [] for oid in ids}
    for oid in sorted(bodies):
        found = owner(oid)
        if found is not None:
            owned[found].append(bodies[oid])
    features: dict[int, frozenset[str]] = {}
    for oid in sorted(registries):
        found = owner(oid)
        if found is not None:
            features.setdefault(found, registries[oid])

    design.components = [
        Component(
            oid=oid,
            name=_trailing_name(body, by_id[oid]),
            limit=ids[index + 1] if index + 1 < len(ids) else None,
            bodies=tuple(owned[oid]),
            features=features.get(oid, frozenset()),
        )
        for index, oid in enumerate(ids)
    ]
    design.bodies = tuple(bodies[oid] for oid in sorted(bodies))
    return design
