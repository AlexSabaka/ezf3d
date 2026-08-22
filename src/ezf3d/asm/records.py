"""Entity graph over a tokenized ASM stream.

SAB pointers are *record indices*: token ``POINTER=10`` means "entity 10 in
this file", and ``-1`` is null.  That makes resolution a list lookup, and the
whole file a flat array of entities with an implicit graph over it.

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

#: Record that closes an ASM stream.
END_MARKER = ("End", "of", "ASM", "data")
#: Record that closes the rollback-history section of a ``.smbh`` body.
HISTORY_MARKER = ("End", "of", "ASM", "History", "Section")

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
        """Follow a pointer field; ``None`` for null or out-of-range."""
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


def _starts_with(types: tuple[str, ...], marker: tuple[str, ...]) -> bool:
    return len(types) >= len(marker) and types[: len(marker)] == marker


def _ends_with(types: tuple[str, ...], marker: tuple[str, ...]) -> bool:
    return len(types) >= len(marker) and types[-len(marker) :] == marker


def parse(data: bytes) -> AsmModel:
    """Parse an ASM/SAB stream into an :class:`AsmModel`."""
    header = read_header(data)
    model = AsmModel(header=header)
    for index, record in enumerate(tokenize(data, header.body_offset, header.word_size)):
        types = _split_types(record)
        # A body with history can pack both markers into one record, with no
        # record terminator between them, so neither test excludes the other.
        is_history = _starts_with(types, HISTORY_MARKER)
        is_end = _ends_with(types, END_MARKER)
        if is_history:
            model.has_history = True
        if is_end:
            model.terminated = True
        if is_history or is_end:
            continue
        model.entities.append(Entity(index=index, types=types, tokens=record))
    return model
