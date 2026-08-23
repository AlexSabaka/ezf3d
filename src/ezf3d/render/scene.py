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
from ezf3d.mesh.mesh import Mesh
from ezf3d.mesh.polyline import DEFAULT_CHORD_TOLERANCE, wireframe
from ezf3d.mesh.tessellate import Tessellation, tessellate
from ezf3d.model.document import Body, Document
from ezf3d.ogs.cache import GraphicsCache
from ezf3d.ogs.stream import OgsError


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
    chosen: list[Body] = chosen_bodies(document, body)
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


def chosen_bodies(document: Document, body: str | None) -> list[Body]:
    """Bodies of *document*, or the one whose UUID starts with *body*."""
    chosen = [
        candidate
        for child in document.documents()
        for candidate in child.bodies
        if body is None or candidate.uuid.startswith(body)
    ]
    if body is not None and not chosen:
        raise KeyError(f"no body matching {body!r}")
    return chosen


def build_mesh(
    document: Document,
    *,
    tolerance: float = DEFAULT_CHORD_TOLERANCE,
    body: str | None = None,
) -> Tessellation:
    """Tessellate the document's bodies into one :class:`Tessellation`.

    Like :func:`build_scene`, this places nothing: bodies are in their own
    local coordinates until component placement is decoded.
    """
    total = Tessellation()
    merged = Mesh()
    for item in chosen_bodies(document, body):
        part = tessellate(Shape(item.model()), tolerance)
        total.faces_meshed += part.faces_meshed
        total.unsupported += part.unsupported
        total.solids += part.solids
        total.watertight_solids += part.watertight_solids
        total.closed_candidates += part.closed_candidates
        total.faces_over_tolerance += part.faces_over_tolerance
        total.max_deviation = max(total.max_deviation, part.max_deviation)
        merged = merged.merged(part.mesh)
    total.mesh = merged
    return total


#: How far apart two points may be and still be the same corner, in cm.
#: Cached vertices are float32, so a corner 10 cm from the origin carries
#: roughly 1e-06 cm of representation error.
_COINCIDENT = 1e-4

#: Fraction of cached corners that must land on a body's vertices before the
#: cache is said to draw that body on its own.
_IDENTIFY_SHARE = 0.95

#: Fraction below which a body is not even a contributor to the cache.
_CONTRIBUTE_SHARE = 0.05

#: Corners sampled when identifying.  The comparison is brute force against
#: every ``point`` record of every body, and a few hundred settle a share.
_CORNER_SAMPLE = 600


@dataclass(slots=True)
class CachedGeometry:
    """Fusion's own mesh for one body, and what it does and does not cover.

    The cache holds a single body's worth of geometry and does not name which
    body that is, so :func:`build_cached` identifies it by face count and
    bounds against the document's B-Reps.  When no body matches, or when the
    one that does has more faces than the cache holds, *covers_body* is false
    and the mesh is a fragment: correct as far as it goes, and not the whole
    solid.  Callers that need the whole solid should fall back to tessellating.
    """

    mesh: Mesh = field(default_factory=Mesh)
    faces: int = 0
    edges: int = 0
    triangles: int = 0
    segments: np.ndarray = field(default_factory=lambda: np.zeros((0, 2, 3)))
    #: UUID of the body the cache draws, when exactly one matches.
    body: str | None = None
    #: Every body whose vertices the cache's corners land on.  A design saved
    #: with history holds a body whose points are a superset of the plain
    #: body's, so both can match and neither can be ruled out from corners
    #: alone.
    candidates: list[str] = field(default_factory=list)
    #: Live faces of the largest candidate — the conservative comparison for
    #: :attr:`faces`, since using a partial cache as if it were whole is the
    #: failure worth avoiding.
    body_faces: int = 0
    #: Bodies contributing any appreciable share of the cached corners.  More
    #: than one means the cache draws an assembly: the ``.f3z`` sample's cache
    #: takes its corners from ten bodies, and belongs to no single one.
    contributors: int = 0
    #: Fraction of cached corners that are a vertex of *some* body in the
    #: document.  One, in every sample — which is what says the cache and the
    #: B-Reps describe the same geometry in the same coordinates.
    corner_coverage: float = 0.0
    #: True when the cache holds a mesh for every face of the body it draws.
    covers_body: bool = False
    #: Bytes of the vertex blob left unread and read twice — zero when the
    #: scene graph was walked completely.
    gap: int = 0
    overlap: int = 0

    @property
    def is_empty(self) -> bool:
        return self.mesh.is_empty


def open_cache(document: Document) -> GraphicsCache | None:
    """The graphics cache of *document*'s primary asset, if it has a readable one."""
    for child in document.documents():
        asset = child.primary
        if asset is None or not asset.has_graphics_cache:
            continue
        try:
            cache = asset.graphics_cache()
        except OgsError:
            continue
        if cache is not None and not cache.is_empty:
            return cache
    return None


def build_cached(
    document: Document, *, body: str | None = None, identify: bool = True
) -> CachedGeometry | None:
    """Read Fusion's cached mesh, or ``None`` when the document carries none.

    *identify* parses the document's B-Reps to work out which body the cache
    draws and whether it covers all of it.  That is the whole cost of the
    check — the tessellation itself is skipped either way — but it can be
    turned off when the caller only wants the triangles.
    """
    cache = open_cache(document)
    if cache is None:
        return None
    gap, overlap = cache.coverage()
    result = CachedGeometry(
        mesh=cache.mesh(),
        faces=cache.face_count,
        edges=cache.edge_count,
        triangles=cache.triangle_count,
        segments=cache.segments(),
        gap=gap,
        overlap=overlap,
    )
    if identify:
        shares, coverage = _corner_shares(document, cache, body)
        result.corner_coverage = coverage
        result.contributors = sum(1 for share, _, _ in shares if share >= _CONTRIBUTE_SHARE)
        found = [(uuid, faces) for share, uuid, faces in shares if share >= _IDENTIFY_SHARE]
        if found:
            result.candidates = [uuid for uuid, _ in found]
            result.body = found[0][0] if len(found) == 1 else None
            result.body_faces = max(count for _, count in found)
            result.covers_body = result.faces >= result.body_faces
    return result


def _corner_shares(
    document: Document, cache: GraphicsCache, body: str | None
) -> tuple[list[tuple[float, str, int]], float]:
    """Per-body share of the cached corners, and the share any body accounts for.

    A cached edge is a polyline between two B-Rep vertices, so its endpoints
    are ``point`` records of whatever is being drawn — a far sharper key than
    either face count (two bodies can share one) or bounding box (a body of
    revolution bulges well past its own vertices: SUCKER's funnel reaches 3.39
    cm in *y* where its vertices stop at 2.30).

    Several bodies can score highly and the caller is told rather than sold a
    guess.  It happens two ways: a design saved with history holds a body
    whose points are a superset of the plain body's, and an assembly's cache
    draws many bodies at once.
    """
    corners = _cached_corners(cache)
    if not len(corners):
        return [], 0.0
    covered = np.zeros(len(corners), dtype=bool)
    shares: list[tuple[float, str, int]] = []
    for candidate in chosen_bodies(document, body):
        model = candidate.model()
        points = np.array(
            [
                pos
                for entity in model.entities
                if entity.name == "point"
                for pos in entity.positions()
            ],
            dtype=float,
        )
        if not len(points):
            continue
        hit = _coincident(corners, points)
        covered |= hit
        share = float(hit.mean())
        if share >= _CONTRIBUTE_SHARE:
            shares.append((share, candidate.uuid, sum(1 for _ in Shape(model).faces())))
    shares.sort(key=lambda row: -row[0])
    return shares, float(covered.mean())


def _cached_corners(cache: GraphicsCache, limit: int = _CORNER_SAMPLE) -> np.ndarray:
    """Endpoints of the cached edge polylines, thinned to at most *limit*."""
    corners = cache.edge_endpoints()
    if len(corners) > limit:
        corners = corners[:: len(corners) // limit + 1]
    return corners


def _coincident(corners: np.ndarray, points: np.ndarray, chunk: int = 256) -> np.ndarray:
    """Which of *corners* coincide with a point of *points*.

    The cache holds float32, so "coincide" is a micron rather than exact.
    """
    found = np.zeros(len(corners), dtype=bool)
    for start in range(0, len(corners), chunk):
        block = corners[start : start + chunk]
        distance = np.linalg.norm(block[:, None, :] - points[None, :, :], axis=2)
        found[start : start + chunk] = distance.min(axis=1) < _COINCIDENT
    return found


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
