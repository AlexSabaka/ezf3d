"""Assemble a document's geometry into something renderable.

**Bodies are drawn in their own local coordinates.**  A Fusion design places
its components with occurrence transforms held in the design segment, not in
the ASM body files — 16 of one sample's 22 bodies have bounding boxes straddling
the origin because each is modelled about its own frame.  Rendering the whole
document therefore stacks the parts on top of each other rather than assembling
them; :attr:`Scene.unplaced` says so, and ``--body`` renders one part correctly.
Assembly placement arrives with the design graph in Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ezf3d.asm.brep import Shape
from ezf3d.mesh.polyline import DEFAULT_CHORD_TOLERANCE, wireframe
from ezf3d.model.document import Body, Document


@dataclass(slots=True)
class Scene:
    """Line geometry gathered from one or more bodies."""

    segments: np.ndarray = field(default_factory=lambda: np.zeros((0, 2, 3)))
    bodies: int = 0
    polylines: int = 0
    #: Edges drawn as a straight chord because their curve is not evaluable yet.
    approximated: int = 0
    #: Edges left out for the same reason, which is the default.
    omitted: int = 0
    skipped: int = 0
    #: True when more than one body is drawn without assembly placement.
    unplaced: bool = False

    @property
    def is_empty(self) -> bool:
        return not len(self.segments)

    def points(self) -> np.ndarray:
        return self.segments.reshape(-1, 3)

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self.is_empty:
            return None
        points = self.points()
        return points.min(axis=0), points.max(axis=0)


def build_scene(
    document: Document,
    *,
    tolerance: float = DEFAULT_CHORD_TOLERANCE,
    body: str | None = None,
    chords: bool = False,
) -> Scene:
    """Discretise the document's bodies into line segments.

    *body* selects a single body by UUID prefix, which is the only way to get a
    correctly positioned picture until component placement is decoded.
    """
    chosen: list[Body] = []
    for child in document.documents():
        for candidate in child.bodies:
            if body is None or candidate.uuid.startswith(body):
                chosen.append(candidate)
    if body is not None and not chosen:
        raise KeyError(f"no body matching {body!r}")

    scene = Scene(bodies=len(chosen))
    pieces: list[np.ndarray] = []
    for item in chosen:
        frame = wireframe(Shape(item.model()), tolerance, chords=chords)
        scene.polylines += frame.count
        scene.approximated += frame.approximated
        scene.omitted += frame.omitted
        scene.skipped += frame.skipped
        segments = frame.segments()
        if len(segments):
            pieces.append(segments)
    if pieces:
        scene.segments = np.concatenate(pieces)
    scene.unplaced = len(chosen) > 1
    return scene


def contact_sheet(tiles: list[np.ndarray], columns: int) -> np.ndarray:
    """Tile equally sized images into a grid, padding the last row."""
    if not tiles:
        raise ValueError("no tiles")
    height, width, channels = tiles[0].shape
    rows = (len(tiles) + columns - 1) // columns
    sheet = np.zeros((rows * height, columns * width, channels), dtype=tiles[0].dtype)
    sheet[:] = tiles[0][0, 0]
    for index, tile in enumerate(tiles):
        r, c = divmod(index, columns)
        sheet[r * height : (r + 1) * height, c * width : (c + 1) * width] = tile
    return sheet
