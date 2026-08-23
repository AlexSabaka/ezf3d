"""Which material and appearance a component or body is assigned.

The design stream does not store materials; it stores *assignments*, and the
materials themselves live in the asset's ``.protein`` package — a nested ZIP of
Autodesk Protein assets. This reads the assignments, and reads the package's
table of contents far enough to say what kind of asset each one names. It does
not decode the property streams inside, which is where a material's readable
name sits.

An assignment is eleven bytes and four wide strings::

    0x01 0x01 <u64 0> 0x00     a flagged reference
    wstr asset       the protein asset id, e.g. "0C7D1000-E2AC-…-F6DFEEDF746D"
    wstr library     the library it came from, or "" when the next slot names it
    wstr material    "i4 Custom Materials|PLA" — written only for a user library
    wstr appearance  "PrismMaterial-018"

The eleven bytes read two ways and the samples cannot separate them: a flag
byte and a reference to object 0 (``AssetSettings``), or a reference to object
1 (``ProteinAssetManager``) and two spare bytes.  Every one of the six
documents numbers those two roots 0 and 1, so both readings are consistent
everywhere; :data:`ANCHOR` is therefore matched as a literal signature and no
claim is made about which object it names.

**Only components and design bodies carry one.**  The holders partition
exactly: 1 component and 12 bodies in SUCKER, 11 and 33 in Robotic_Bhujha, 7
and 138 across the ``.f3z`` package, with nothing else.  The wheel is the
exception only because it has no ``BodiesRoot`` — its two non-component
holders are the base feature's body records.

Two checks come from a different file in the archive.  Every asset id an
assignment names is one the ``.protein`` package's table of contents declares,
and every appearance name it writes appears in that package too — 6 documents,
no exceptions.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
from bisect import bisect_right
from dataclasses import dataclass, field

from ezf3d.streams.segment import Segment

#: The eleven bytes an assignment opens with.  See the module docstring for
#: why this is a literal rather than a decoded reference.
ANCHOR = b"\x01\x01" + b"\x00" * 9

#: Wide strings an assignment carries, in order.
SLOTS = 4

#: A protein asset id.  Fusion appends ``_Post2015`` to an asset it migrated,
#: sometimes more than once, so the suffix is part of the id.
ASSET_ID_RE = re.compile(r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}(?:_[A-Za-z0-9]+)*")

#: Longest wide string read as one of an assignment's four slots.
_MAX_SLOT = 256

#: The entry in a ``.protein`` package that lists its assets.
CATALOGUE_ENTRY = "AssetTableOfContents.bin"

#: Asset categories the table of contents uses.  Only these are treated as a
#: category, so a stray string beside an id cannot become one.
CATEGORIES = frozenset(
    {"materialappearance", "physicalmaterial", "Thermal", "Structural", "Appearance"}
)

#: Category of the assets a component or body is actually assigned.  Every
#: assignment in every sample names one of these, never an appearance asset —
#: the appearance travels as a name in the fourth slot instead.
PHYSICAL = "physicalmaterial"


@dataclass(frozen=True, slots=True)
class Assignment:
    """One component's or body's material, as the design records it."""

    #: Object that carries it — a component, or a body under ``BodiesRoot``.
    oid: int
    #: Protein asset id, which the package's catalogue declares.
    asset: str
    #: Library the asset came from, as a guid.  Empty when the design writes
    #: :attr:`material` instead, which is what a user library does.
    library: str = ""
    #: ``library|material`` as the designer sees it, e.g.
    #: ``i4 Custom Materials|PLA``.  Empty for an Autodesk-library material,
    #: whose readable name lives in the ``.protein`` package and is not read.
    material: str = ""
    #: Appearance name, e.g. ``PrismMaterial-018``.
    appearance: str = ""

    @property
    def is_named(self) -> bool:
        """True when the design itself says what the material is called."""
        return bool(self.material)


@dataclass(slots=True)
class Materials:
    """A document's assignments, and the assets its package declares."""

    assignments: list[Assignment] = field(default_factory=list)
    #: Protein asset id to category, from the package's table of contents.
    catalogue: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.assignments)

    def __iter__(self):
        return iter(self.assignments)

    def by_object(self) -> dict[int, Assignment]:
        """The assignment each object carries.  One each, in every sample."""
        return {assignment.oid: assignment for assignment in self.assignments}

    def assets(self) -> dict[str, str]:
        """The assets actually used, and the category the package gives each."""
        return {a.asset: self.catalogue.get(a.asset, "") for a in self.assignments}

    def check(self) -> tuple[str, ...]:
        """Asset ids an assignment names that the package does not declare.

        The two come from different files — the design stream and the nested
        ``.protein`` ZIP — so agreement is a real check.  Empty for all six
        documents; a non-empty catalogue is required, or the check would pass
        vacuously on a document whose package was not read.
        """
        if not self.catalogue:
            return ()
        return tuple(sorted({a.asset for a in self.assignments if a.asset not in self.catalogue}))


def _wstr_at(body: bytes, pos: int, limit: int) -> tuple[str, int] | None:
    if pos + 4 > limit:
        return None
    count = struct.unpack_from("<I", body, pos)[0]
    if count > _MAX_SLOT or pos + 4 + 2 * count > limit:
        return None
    raw = body[pos + 4 : pos + 4 + 2 * count]
    if any(raw[2 * i + 1] for i in range(count)):
        return None
    return raw.decode("utf-16-le"), pos + 4 + 2 * count


def read_assignments(segment: Segment) -> list[Assignment]:
    """Every material assignment in a design segment, in object order.

    The anchor also matches runs of zero padding, so a run counts only when it
    has all four slots and the first is an asset id.  Without that, SUCKER
    reports 22 assignments instead of 13 — the extra nine being empty strings
    read out of alignment.
    """
    body = segment.bulk.body
    items = segment.objects()
    if not items:
        return []
    starts = [item.offset for item in items]

    out: list[Assignment] = []
    at = body.find(ANCHOR)
    while at >= 0:
        index = bisect_right(starts, at) - 1
        limit = items[index].end if index >= 0 else len(body)
        pos = at + len(ANCHOR)
        slots: list[str] = []
        while len(slots) < SLOTS:
            found = _wstr_at(body, pos, limit)
            if found is None:
                break
            slots.append(found[0])
            pos = found[1]
        if len(slots) == SLOTS and ASSET_ID_RE.fullmatch(slots[0]):
            out.append(
                Assignment(
                    oid=items[index].oid,
                    asset=slots[0],
                    library=slots[1],
                    material=slots[2],
                    appearance=slots[3],
                )
            )
        at = body.find(ANCHOR, max(at + 1, pos if slots else at + 1))
    return out


def read_catalogue(blob: bytes) -> dict[str, str]:
    """``asset id -> category`` from a ``.protein`` package.

    The table of contents is a flat run of ``str8`` values — the same
    length-prefixed encoding the Neutron streams use — so an id followed by a
    known category is one entry.  Nothing else in the package is decoded: a
    material's readable name ("Steel") sits in its property streams, which need
    the schema documents beside them to make sense of.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        return {}

    catalogue: dict[str, str] = {}
    for name in archive.namelist():
        if not name.endswith(CATALOGUE_ENTRY):
            continue
        data = archive.read(name)
        previous = ""
        pos = 0
        while pos + 4 <= len(data):
            size = int.from_bytes(data[pos : pos + 4], "little")
            if 1 <= size <= _MAX_SLOT and pos + 4 + size <= len(data):
                raw = data[pos + 4 : pos + 4 + size]
                if all(0x20 <= char < 0x7F for char in raw):
                    value = raw.decode("ascii")
                    if value in CATEGORIES and ASSET_ID_RE.fullmatch(previous):
                        catalogue.setdefault(previous, value)
                    previous = value
                    pos += 4 + size
                    continue
            pos += 1
    return catalogue
