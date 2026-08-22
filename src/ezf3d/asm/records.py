"""Entity graph over a tokenized ASM stream.

SAB pointers are indices into the file's **main-section** entities: token
``POINTER=10`` means "the eleventh model entity in this file", and ``-1`` is
null.  Two things make that different from "the eleventh record":

*Section markers are prefixes, not records of their own.*  ``Begin of ASM
History Data`` and ``End of ASM History Section`` are five type tokens glued
onto the front of a real entity, with no record terminator between them -- the
history-end marker is followed by a ``face`` and its fields in the same record.
Dropping such a record would lose an entity and shift every index after it.

*The rollback-history block is not addressable.*  A ``.smbh`` body embeds its
history section mid-file (a ``history_stream`` followed by ``delta_state``
records).  Those records occupy the stream but not the pointer space, and their
own pointers live in a separate index space, so they must not be walked as
topology.

:attr:`AsmModel.entities` therefore holds exactly the addressable entities, in
pointer order, and history records go to :attr:`AsmModel.history`.

An entity's leading tokens are its class chain, written most-derived first::

    ENTITY_TYPE_EX 'plane'   ENTITY_TYPE 'surface'   ...fields...
    ENTITY_TYPE    'face'                            ...fields...

so :attr:`Entity.name` is the concrete class and :attr:`Entity.base` the ASM
base class.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ezf3d.asm.header import AsmHeader, read_header
from ezf3d.asm.tokens import Record, Tag, tokenize

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Null pointer value.
NULL = -1

#: Type chain of the record that closes an ASM stream.  Unlike the section
#: markers this one stands alone, with no entity behind it.
END_MARKER = ("End", "of", "ASM", "data")
#: Prefix that opens the rollback-history section of a ``.smbh`` body.
HISTORY_BEGIN_MARKER = ("Begin", "of", "ASM", "History", "Data")
#: Prefix that closes it.  The entity behind this prefix is a model entity
#: again -- the marker ends the section, it does not belong to it.
HISTORY_END_MARKER = ("End", "of", "ASM", "History", "Section")

_TYPE_TAGS = (Tag.ENTITY_TYPE, Tag.ENTITY_TYPE_EX)


@dataclass(slots=True)
class Entity:
    """One ASM record, with its class chain split out from its fields."""

    index: int
    types: tuple[str, ...]
    tokens: Record

    @property
    def name(self) -> str:
        """Concrete class, e.g. ``plane``, ``face``, ``ATTRIB_CUSTOM``."""
        return self.types[0] if self.types else ""

    @property
    def base(self) -> str:
        """ASM base class, e.g. ``surface`` for a ``plane``."""
        return self.types[-1] if self.types else ""

    @property
    def fields(self) -> Record:
        """Tokens after the class chain."""
        return self.tokens[len(self.types) :]

    def pointers(self) -> list[int]:
        """Every pointer field, in order, nulls included."""
        return [int(v) for tag, v in self.fields if tag == Tag.POINTER]  # type: ignore[arg-type]

    def links(self) -> list[int]:
        """Non-null pointer targets."""
        return [p for p in self.pointers() if p != NULL]

    def positions(self) -> list[tuple[float, float, float]]:
        return [v for tag, v in self.fields if tag == Tag.POSITION]  # type: ignore[misc]

    def strings(self) -> list[str]:
        return [str(v) for tag, v in self.fields if tag in (Tag.STR, Tag.LITERAL_STR)]


def _split_types(record: Record) -> tuple[str, ...]:
    names: list[str] = []
    for tag, value in record:
        if tag in _TYPE_TAGS:
            names.append(str(value))
        else:
            break
    return tuple(names)


@dataclass(slots=True)
class AsmModel:
    """A parsed ASM body file."""

    header: AsmHeader
    entities: list[Entity] = field(default_factory=list)
    #: Records of the rollback-history block.  Kept for inspection, excluded
    #: from :attr:`entities` because pointers do not address them.
    history: list[Entity] = field(default_factory=list)
    #: True when the stream carried a rollback-history section (``.smbh``).
    has_history: bool = False
    #: True when the walk reached ``End of ASM data``.  A body that parses
    #: without error but is not terminated was only partly understood, so this
    #: is the honest check that the token grammar covered the whole file.
    terminated: bool = False

    def __len__(self) -> int:
        return len(self.entities)

    def __getitem__(self, index: int) -> Entity:
        return self.entities[index]

    def resolve(self, pointer: int) -> Entity | None:
        """Follow a pointer field; ``None`` for null or out-of-range.

        :attr:`entities` is in pointer order by construction, so this is a
        list lookup -- see the module docstring for why that is not the same
        as indexing by record ordinal.
        """
        if pointer == NULL or not 0 <= pointer < len(self.entities):
            return None
        return self.entities[pointer]

    def of_type(self, *names: str) -> Iterator[Entity]:
        """Entities whose concrete or base class is any of *names*."""
        wanted = set(names)
        for entity in self.entities:
            if entity.name in wanted or entity.base in wanted:
                yield entity

    def counts(self) -> Counter[str]:
        """Census by concrete class name."""
        return Counter(e.name for e in self.entities if e.name)


def _strip_prefix(
    types: tuple[str, ...], record: Record, marker: tuple[str, ...]
) -> tuple[tuple[str, ...], Record] | None:
    """Remove *marker* from the front of a record, or ``None`` if absent."""
    if len(types) < len(marker) or types[: len(marker)] != marker:
        return None
    return types[len(marker) :], record[len(marker) :]


def parse(data: bytes) -> AsmModel:
    """Parse an ASM/SAB stream into an :class:`AsmModel`."""
    header = read_header(data)
    model = AsmModel(header=header)
    in_history = False

    for index, record in enumerate(tokenize(data, header.body_offset, header.word_size)):
        types = _split_types(record)

        stripped = _strip_prefix(types, record, HISTORY_BEGIN_MARKER)
        if stripped is not None:
            in_history = True
            model.has_history = True
            types, record = stripped
        else:
            stripped = _strip_prefix(types, record, HISTORY_END_MARKER)
            if stripped is not None:
                in_history = False
                model.has_history = True
                types, record = stripped

        # A short body can pack a section marker and the terminator into one
        # record, so the terminator is tested after any prefix is removed.
        if types == END_MARKER:
            model.terminated = True
            continue

        entity = Entity(index=index, types=types, tokens=record)
        (model.history if in_history else model.entities).append(entity)

    return model
