"""The timeline: which features a design has, and in what order.

A feature is an ordinary bulk object with a recognisable tail::

    wstr guid              all-zero for most features
    0x00 0x00 0x00
    u32 n, n x reference   the inputs -- sketches, faces, bodies it consumes
    u32 token              0xFFFFFFFF where Fusion wrote none
    wstr name              "Extrude", "Fillet", "Base Feature"; may be empty

The name is the label Fusion shows in the timeline, and a renamed feature
carries the name the user typed.

**Order is not creation order.** Every feature is followed in the object index
by a small *timeline item*, and one object lists those items in the order the
timeline runs.  In SUCKER that list interleaves ids 1270, 12657, 12825 -- a
feature created late sitting ninth -- which is what a designer dragging the
timeline marker back and inserting there produces.  Ordering by object id
would put it last, so the list is read rather than reconstructed.

Nothing points from an item back to its feature; the feature is simply the
object before it in the index.  That is what makes the list identifiable:
:func:`read_timeline` accepts only a list whose every entry resolves that way,
and takes the longest.  It resolves 3 of 3, 58 of 58, 225 of 225 and 400 of
400 across the four samples, and there is exactly one such list per design.

Two independent checks say the reading is right rather than merely
self-consistent.  Every name maps to a kind the registries declare -- see
:data:`KIND_ALIASES` for the labels Fusion spells differently -- and no kind
appears in the timeline more often than the registry's own counter says was
ever issued (:meth:`~ezf3d.streams.segment.BulkStream.feature_counters`).
Both hold for every sample.
"""

from __future__ import annotations

import re
import struct
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from ezf3d.model.parameters import Parameter
from ezf3d.streams.primitives import scan_strings
from ezf3d.streams.segment import BulkObject, Segment

#: Bytes between a feature's guid and its input count.
FEATURE_GAP = 3

#: A reference on the wire: ``0x01``, a ``u64`` object id, two spare bytes.
_STRIDE = 11
_REFERENCE = 0x01

#: Longest reference list treated as real, and longest name.
_MAX_INPUTS = 4096
_MAX_NAME = 128

GUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")

#: Bytes into an object searched for a reference count.  The lists seen start
#: it at 10 and 17; the margin covers a revision that moves it.
_HEADER_SEARCH = 48

#: Timeline labels the general rules cannot reach.  Most labels are the
#: registry kind with ``Feature`` dropped -- ``Extrude`` for
#: ``ExtrudeFeature`` -- or the same name with its spaces removed, and those
#: need no entry here; ``JointOrigin`` and ``MotionLink`` were listed until a
#: test showed the plain rule already found them.  What is left is where
#: Fusion's UI and its serializer genuinely disagree, and the counter check is
#: what says each mapping is right rather than merely plausible.
KIND_ALIASES = {
    "Assemble": "JointAssembleFeature",
    "Body->Comp": "ComponentFromBodiesFeature",
    "C-Pattern": "CircularPattern",
    "CopyPasteBodies": "PasteBodies",
    "Fillet": "FilletEdgeFeature",
    "Mirror": "MirrorPattern",
    "MoveFace": "MoveFacesFeature",
    "Position": "SnapshotFeature",
    "R-Pattern": "RectangularPattern",
    "RemoveBody": "DeleteBody",
    "Split": "SplitBodyFeature",
}


#: Bytes between a record's revision string and the extrude settings flag:
#: a ``u64``, a ``u32``, a spare byte, a ``u32`` and three spare.
SETTINGS_GAP = 20

#: What an extrude does with the material it sweeps.  ``Join``, ``Cut`` and
#: ``NewBody`` are pinned by a Fusion readout of SUCKER's eight extrudes; 3 is
#: never seen in the samples and is left unnamed rather than guessed at.
EXTRUDE_OPERATIONS = {1: "Join", 2: "Cut", 4: "NewBody"}

#: Which way it goes.  ``OneSide`` and ``Symmetric`` come from the same
#: readout.  ``TwoSides`` is corroborated instead by the parameters: the one
#: record in the samples carrying a 2 is also the only extrude whose roles
#: include ``AgainstDistance`` and ``Side2TaperAngle``, and those two facts are
#: written into different parts of the file.
EXTRUDE_DIRECTIONS = {1: "OneSide", 2: "TwoSides", 3: "Symmetric"}

#: Codes accepted while looking for the settings block.  Anything outside them
#: means the anchor landed on the wrong revision string, which is what keeps a
#: loft's and a work plane's records from reading as extrude settings.
_OPERATION_CODES = frozenset({1, 2, 3, 4})
_DIRECTION_CODES = frozenset({1, 2, 3})


@dataclass(frozen=True, slots=True)
class Extrude:
    """What an extrude does, as opposed to by how much."""

    #: ``Join``, ``Cut``, ``NewBody``, or ``""`` for a code with no name yet.
    operation: str
    #: ``OneSide``, ``TwoSides`` or ``Symmetric``.
    direction: str
    #: The three ``u32`` as read.  The third is 2 for all but one record in the
    #: samples and is not understood, so it is carried rather than interpreted.
    codes: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Feature:
    """One entry of the timeline."""

    #: Object id, which is creation order -- not timeline order.
    oid: int
    #: Position in the timeline, counting from zero.
    index: int
    #: The label Fusion shows, or the name the user gave it.  Empty when
    #: Fusion wrote none; 16 of Robotic_Bhujha's 225 are like that.
    name: str
    #: Registry kind this label names, or ``""`` when nothing declares it.
    kind: str
    #: The timeline item that put it here -- the object right after it.
    item: int
    #: Objects it consumes: sketches, faces, bodies, other features.
    inputs: tuple[int, ...] = ()
    #: The numbers that drive it, in object order.  Filled in only when
    #: :func:`read_timeline` is given the design's parameters; the feature
    #: record itself does not name them.
    parameters: tuple[Parameter, ...] = ()
    #: Operation and direction, for an extrude.  ``None`` for every other kind:
    #: no other kind's record reads as one at this offset.
    extrude: Extrude | None = None

    @property
    def is_named(self) -> bool:
        return bool(self.name)

    def role(self, name: str) -> Parameter | None:
        """The parameter filling one slot, e.g. ``AlongDistance``."""
        return next((p for p in self.parameters if p.role == name), None)


@dataclass(slots=True)
class Timeline:
    """A design's ordered features, and the registry they are checked against."""

    #: Object id of the list, or 0 when a design has no timeline.
    oid: int = 0
    features: list[Feature] = field(default_factory=list)
    #: Per-kind counters the registries declare — an ever-created upper bound.
    declared: Counter[str] = field(default_factory=Counter)
    #: Named features the design holds that the list does *not*, by kind.
    #: Empty for a single-component design; Robotic_Bhujha's assembly-level
    #: work — joint origins, component creation and placement — sits here,
    #: which says this list is the modelling timeline and not the whole of
    #: what Fusion shows.  Counted rather than quietly dropped.
    outside: Counter[str] = field(default_factory=Counter)
    #: Every named feature-shaped object in the design, by id -- the ones the
    #: list holds and the ones :attr:`outside` counts alike.  Finding the list
    #: at all means building this, and the wider set is what
    #: :mod:`ezf3d.model.sketch` needs: Focuser Mk1 has 39 sketches of which
    #: the list carries 33.
    named: dict[int, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self):
        return iter(self.features)

    def census(self) -> Counter[str]:
        """How many live features of each kind, by registry kind."""
        return Counter(feature.kind for feature in self.features if feature.kind)

    def unnamed(self) -> int:
        return sum(1 for feature in self.features if not feature.name)

    def check(self) -> tuple[tuple[str, ...], tuple[tuple[str, int, int], ...]]:
        """``(labels no registry declares, kinds that outrun their counter)``.

        The second is the sharper of the two: a counter is what Fusion counts
        its own labels up with, so a timeline holding more extrudes than were
        ever issued would mean the list is not the timeline.
        """
        unknown = sorted(
            {feature.name for feature in self.features if feature.name and not feature.kind}
        )
        census = self.census()
        over = tuple(
            (kind, live, self.declared.get(kind, 0))
            for kind, live in sorted(census.items())
            if live > self.declared.get(kind, 0)
        )
        return tuple(unknown), over


def kind_of(name: str, declared: Counter[str] | dict[str, int]) -> str:
    """The registry kind *name* labels, or ``""`` when none matches.

    Tried in order: the alias table, the name itself, the name with spaces
    removed, and that plus ``Feature`` — which is how Fusion spells most of
    them (``Extrude`` for ``ExtrudeFeature``).
    """
    if not name:
        return ""
    bare = name.replace(" ", "")
    for candidate in (KIND_ALIASES.get(name, ""), name, bare, f"{bare}Feature"):
        if candidate and candidate in declared:
            return candidate
    return ""


def read_extrude(body: bytes, item: BulkObject) -> Extrude | None:
    """An extrude's operation and direction, or ``None``.

    The settings sit a fixed distance past the record's revision string, and a
    record can carry several of those -- two of Robotic_Bhujha's extrudes nest
    eleven -- so every one is tried and the block is accepted only when its
    codes are ones an extrude uses.  That range check is the whole of the
    safety: without it a loft and four work planes read as extrudes.

    It found the block in **214 of 214** extrudes across the four samples and
    in no record of any other kind.
    """
    for found in scan_strings(body, start=item.offset, end=item.end, min_len=3):
        if found.kind != "str8" or not found.value.isdigit():
            continue
        at = found.end + SETTINGS_GAP
        if at + 13 > item.end or body[at] != _REFERENCE:
            continue
        codes = struct.unpack_from("<3I", body, at + 1)
        if codes[0] in _OPERATION_CODES and codes[1] in _DIRECTION_CODES:
            return Extrude(
                operation=EXTRUDE_OPERATIONS.get(codes[0], ""),
                direction=EXTRUDE_DIRECTIONS[codes[1]],
                codes=codes,
            )
    return None


def _wstr_at(body: bytes, pos: int, limit: int) -> tuple[str, int] | None:
    if pos + 4 > limit:
        return None
    count = struct.unpack_from("<I", body, pos)[0]
    if count > _MAX_NAME or pos + 4 + 2 * count > limit:
        return None
    raw = body[pos + 4 : pos + 4 + 2 * count]
    if any(raw[2 * i + 1] for i in range(count)):
        return None
    return raw.decode("utf-16-le"), pos + 4 + 2 * count


def _references(body: bytes, pos: int, count: int, limit: int) -> tuple[int, ...] | None:
    """*count* wire references starting at *pos*, or ``None`` if they are not there."""
    if count > _MAX_INPUTS or pos + _STRIDE * count > limit:
        return None
    if any(body[pos + _STRIDE * i] != _REFERENCE for i in range(count)):
        return None
    return tuple(struct.unpack_from("<Q", body, pos + 1 + _STRIDE * i)[0] for i in range(count))


def read_feature(body: bytes, item: BulkObject) -> tuple[str, tuple[int, ...]] | None:
    """``(name, inputs)`` if *item* is shaped like a feature, else ``None``.

    An empty name is accepted -- Fusion leaves one on features it does not
    label -- so this alone is a loose test.  What makes it safe is that
    :func:`read_timeline` only ever asks it about the object *preceding* a
    timeline item.

    A record can hold more than one guid, and the tail only reads correctly
    after one of them.  A **named** parse therefore wins over an unnamed one
    wherever both are available: taking the first guid that merely parses cost
    Robotic_Bhujha all eight of its ``Assemble`` features, which read as
    unlabelled off an earlier guid.
    """
    fallback: tuple[str, tuple[int, ...]] | None = None
    for found in scan_strings(body, start=item.offset, end=item.end, min_len=1):
        if found.kind != "wstr" or not GUID_RE.fullmatch(found.value):
            continue
        at = found.end + FEATURE_GAP
        if at + 4 > item.end:
            continue
        count = struct.unpack_from("<I", body, at)[0]
        inputs = _references(body, at + 4, count, item.end)
        if inputs is None:
            continue
        named = _wstr_at(body, at + 8 + _STRIDE * count, item.end)
        if named is None:
            continue
        name = named[0]
        if name and (GUID_RE.fullmatch(name) or any(char < " " for char in name)):
            continue
        if name:
            return name, inputs
        if fallback is None:
            fallback = (name, inputs)
    return fallback


def read_timeline(segment: Segment, parameters: Iterable[Parameter] | None = None) -> Timeline:
    """Read a design segment's timeline, in order.

    The list is found by shape: an object that is nothing but a count and that
    many references, every one of which is preceded in the index by a
    feature-shaped object.  Ties go to the lowest object id so two reads of the
    same file give the same answer.

    Pass *parameters* to fill in :attr:`Feature.parameters`; see
    :func:`attribute` for the rule and what checks it.  It is optional because
    it costs a second pass over the stream and most callers only want the
    order.
    """
    body = segment.bulk.body
    items = segment.objects()
    declared = segment.bulk.feature_counters()
    if not items:
        return Timeline(declared=declared)

    position = {entry.oid: index for index, entry in enumerate(items)}
    by_id = {entry.oid: entry for entry in items}
    features: dict[int, tuple[str, tuple[int, ...]]] = {}
    for entry in items:
        found = read_feature(body, entry)
        if found is not None:
            features[entry.oid] = found

    best: tuple[int, list[int], tuple[int, ...]] | None = None
    for entry in items:
        found = _longest_list(body, entry, items, position, features)
        if found is None:
            continue
        if best is None or len(found[0]) > len(best[1]):
            best = (entry.oid, found[0], found[1])

    named = {oid: name for oid, (name, _) in features.items() if name}
    if best is None:
        return Timeline(
            declared=declared,
            outside=_outside(features, set(), declared),
            named=named,
        )
    oid, owners, list_items = best
    owned = attribute(features, parameters or ())
    return Timeline(
        oid=oid,
        features=[
            Feature(
                oid=owner,
                index=index,
                name=features[owner][0],
                kind=kind_of(features[owner][0], declared),
                item=list_items[index],
                inputs=features[owner][1],
                parameters=owned.get(owner, ()),
                extrude=(
                    read_extrude(body, by_id[owner])
                    if kind_of(features[owner][0], declared) == "ExtrudeFeature"
                    else None
                ),
            )
            for index, owner in enumerate(owners)
        ],
        declared=declared,
        outside=_outside(features, set(owners), declared),
        named=named,
    )


def attribute(
    features: dict[int, tuple[str, tuple[int, ...]]],
    parameters: Iterable[Parameter],
) -> dict[int, tuple[Parameter, ...]]:
    """Which feature owns which parameter: the nearest **named** one before it.

    The feature record does not name its parameters -- its ``inputs`` list
    reaches one 4 times in 1,438 -- so ownership is positional: ids are issued
    in creation order, and Fusion writes a feature's parameters immediately
    after it.

    What checks a positional rule is that the parameter's ``role`` is the slot
    it fills in the feature that owns it.  ``TaperAngle`` must land on an
    extrude and ``Radius`` on a fillet, and over the twenty commonest roles
    they do 97.6% of the time -- the exceptions being roles that genuinely
    belong to several kinds (``countU`` to three pattern kinds) rather than
    misattributions.

    *features* must be **every** named feature-shaped object, not only the ones
    the timeline lists: restricted to the timeline the agreement falls to 85%,
    because the features 3.4 counts as sitting outside the list fall between.
    """
    named = sorted(oid for oid, (name, _) in features.items() if name)
    if not named:
        return {}
    owned: dict[int, list[Parameter]] = {}
    for parameter in sorted(parameters, key=lambda p: p.oid):
        position = bisect_right(named, parameter.oid) - 1
        if position >= 0:
            owned.setdefault(named[position], []).append(parameter)
    return {oid: tuple(values) for oid, values in owned.items()}


def _outside(
    features: dict[int, tuple[str, tuple[int, ...]]],
    inside: set[int],
    declared: Counter[str],
) -> Counter[str]:
    """Named features the timeline list does not hold, by kind."""
    return Counter(
        kind_of(name, declared) or name
        for oid, (name, _) in features.items()
        if name and oid not in inside
    )


def _longest_list(
    body: bytes,
    entry: BulkObject,
    items: list[BulkObject],
    position: dict[int, int],
    features: dict[int, tuple[str, tuple[int, ...]]],
) -> tuple[list[int], tuple[int, ...]] | None:
    """The longest fully-resolving reference list inside one object.

    The count sits at a different offset per record revision — 17 in the
    ``.f3d`` samples, 10 in one of them — so it is searched for rather than
    assumed, and the resolution requirement is what rejects the wrong guess.
    """
    best: tuple[list[int], tuple[int, ...]] | None = None
    limit = min(_HEADER_SEARCH, max(0, entry.size - 4))
    for start in range(limit):
        at = entry.offset + start
        # The first reference's marker byte rejects almost every offset, and
        # costs one comparison against the four-byte unpack that follows.
        if at + 5 > entry.end or body[at + 4] != _REFERENCE:
            continue
        count = struct.unpack_from("<I", body, at)[0]
        if not 1 <= count <= _MAX_INPUTS:
            continue
        ids = _references(body, at + 4, count, entry.end)
        if ids is None:
            continue
        owners = _resolve(ids, items, position, features)
        if owners and (best is None or len(owners) > len(best[0])):
            best = (owners, ids)
    return best


def _resolve(
    ids: tuple[int, ...],
    items: list[BulkObject],
    position: dict[int, int],
    features: dict[int, tuple[str, tuple[int, ...]]],
) -> list[int] | None:
    """The feature behind each timeline item, or ``None`` if any does not resolve.

    A feature is *always* the object before its item, never the referenced
    object itself.  Allowing the second reading as a fallback let a list of
    body-appearance records — a guid, no inputs, no name — pass as a
    seven-entry timeline in two ``.f3z`` members that have no feature registry
    at all.  All-or-nothing on purpose: a list that only mostly resolves is not
    the timeline.
    """
    owners: list[int] = []
    seen: set[int] = set()
    for oid in ids:
        index = position.get(oid)
        if not index:
            return None
        owner = items[index - 1].oid
        if owner not in features or owner in seen:
            return None
        seen.add(owner)
        owners.append(owner)
    return owners
