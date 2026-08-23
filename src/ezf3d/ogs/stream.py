"""Walking OGS's serialized scene graph.

OGS is Autodesk's *One Graphics System*.  When Fusion saves a design it may
leave the display mesh it was drawing behind, under
``<Asset>/OGS.BlobFolder/OGS/DefaultScene/``: a ``world`` file holding the
scene graph and one or more blobs holding the vertex and index data.

``world`` is a byte stream with no alignment and no type tags — the same
problem the Neutron streams pose, and solved the same way: find the structure
the file cannot help repeating.  Here that structure is the object header.

Every object is written as a ``0x01`` flag byte followed by a length-prefixed
UTF-16LE string naming it, then a payload whose shape depends on the class.
The string is a class name near the root (``GroupNode``, ``VertexFormat``) and
a node name deeper in (``Body``, ``Faces``, ``Face``, ``Edge``); the two are
not distinguished on the wire, and for reading geometry they need not be.

Scanning for that header is a heuristic, so it is worth saying what makes it
trustworthy here rather than merely plausible: the buffer descriptors it finds
tile the vertex blob **exactly**, with no gap and no overlap, in every sample
that has one.  A missed object would leave a gap and an invented one an
overlap.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

#: Every ``world`` seen so far opens with this class name.
MAGIC = "ARenderList"

#: Longest string treated as a name; real ones are short, and the cap keeps a
#: run of zero bytes from being read as a huge string.
_MAX_NAME = 256


class OgsError(ValueError):
    """Raised when a graphics cache is not shaped the way this module expects."""


@dataclass(frozen=True, slots=True)
class Node:
    """One object in the stream, and the span its payload occupies."""

    #: Offset of the ``0x01`` flag byte.
    offset: int
    #: Class or node name.
    name: str
    #: First byte after the name — where the payload starts.
    payload: int
    #: Offset of the next object's flag byte, or end of file.
    end: int

    @property
    def size(self) -> int:
        return self.end - self.payload


def read_wstr(data: bytes, pos: int, limit: int = _MAX_NAME) -> tuple[str, int] | None:
    """A ``u32`` character count then that many UTF-16LE characters.

    Returns ``None`` — rather than raising — when *pos* does not hold one, so
    that callers can use this to probe.  Only printable ASCII is accepted:
    every name and handle in a ``world`` is ASCII, and insisting on it is what
    keeps a run of coordinate bytes from decoding as a plausible string.
    """
    if pos + 4 > len(data):
        return None
    count = int.from_bytes(data[pos : pos + 4], "little")
    if not 1 <= count <= limit:
        return None
    end = pos + 4 + count * 2
    if end > len(data):
        return None
    raw = data[pos + 4 : end]
    if any(raw[i + 1] or not 0x20 <= raw[i] < 0x7F for i in range(0, len(raw), 2)):
        return None
    return raw.decode("utf-16-le"), end


def _is_name(text: str) -> bool:
    """Names start with a letter; handles like ``0x3a94700f8`` do not.

    Angle brackets appear in template-derived class names (``CircleArc<double>``).
    """
    return text[:1].isalpha() and all(c.isalnum() or c in "_<>" for c in text)


def walk(data: bytes) -> Iterator[Node]:
    """Yield every object header in *data*, in file order."""
    found: list[tuple[int, str, int]] = []
    pos = 0
    size = len(data)
    while pos < size:
        if data[pos] == 1:
            probe = read_wstr(data, pos + 1)
            if probe is not None and _is_name(probe[0]):
                found.append((pos, probe[0], probe[1]))
                pos = probe[1]
                continue
        pos += 1
    for index, (offset, name, payload) in enumerate(found):
        end = found[index + 1][0] if index + 1 < len(found) else size
        yield Node(offset=offset, name=name, payload=payload, end=end)


def check_magic(data: bytes) -> None:
    """Raise unless *data* opens the way every ``world`` seen so far does."""
    head = read_wstr(data, 0)
    if head is None or head[0] != MAGIC:
        raise OgsError(f"not an OGS world: expected {MAGIC!r} at offset 0")


def class_census(data: bytes) -> Counter[str]:
    """How many objects of each name the stream holds.

    A ``world`` opens with what looks like a table of class names and counts,
    but it is a nested node list rather than a flat registry and its counts do
    not agree with the stream's contents.  Counting what is actually there is
    both simpler and true.
    """
    return Counter(node.name for node in walk(data))
