"""Cameras for offscreen rendering.

Fusion works Z-up, so the standard views here follow CAD convention rather
than graphics convention: ``front`` looks along +Y with Z upward.

A camera can be *fitted* to a bounding box, which is what makes rendering a
design a one-liner: the caller knows the model's extent from its geometry and
does not have to reason about distances or field of view.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Vec = np.ndarray

#: Direction each named view looks *from*, in a Z-up world.
VIEW_DIRECTIONS: dict[str, tuple[float, float, float]] = {
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "iso": (1.0, -1.0, 0.8),
}

#: Views whose direction is parallel to the default up vector need another one.
_ALTERNATE_UP = np.array([0.0, 1.0, 0.0])
_DEFAULT_UP = np.array([0.0, 0.0, 1.0])


def _normalise(vector: Vec) -> Vec:
    length = float(np.linalg.norm(vector))
    if length == 0.0:
        raise ValueError("zero-length vector")
    return vector / length


@dataclass(slots=True)
class Camera:
    """A view of the model, projecting world points to pixels."""

    eye: Vec
    target: Vec
    up: Vec
    width: int
    height: int
    #: Half-height of the view volume in world units (orthographic only).
    scale: float = 1.0
    perspective: bool = False
    #: Vertical field of view in radians (perspective only).
    fov: float = math.radians(35.0)

    @property
    def forward(self) -> Vec:
        return _normalise(self.target - self.eye)

    def basis(self) -> tuple[Vec, Vec, Vec]:
        """Right, true-up and backward axes of the view frame."""
        backward = _normalise(self.eye - self.target)
        right = np.cross(self.up, backward)
        if float(np.linalg.norm(right)) < 1e-12:
            right = np.cross(_ALTERNATE_UP, backward)
        right = _normalise(right)
        return right, np.cross(backward, right), backward

    def project(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Project world points to pixel coordinates and view depth.

        Returns ``(xy, depth)`` where *xy* is ``(n, 2)`` in pixels with the
        origin top-left, and *depth* is distance along the view axis — smaller
        is nearer.
        """
        right, up, backward = self.basis()
        relative = np.asarray(points, dtype=float) - self.eye
        x = relative @ right
        y = relative @ up
        depth = -(relative @ backward)

        if self.perspective:
            safe = np.where(np.abs(depth) < 1e-9, 1e-9, depth)
            half = math.tan(self.fov / 2.0)
            ndc_x = x / (safe * half * (self.width / self.height))
            ndc_y = y / (safe * half)
        else:
            aspect = self.width / self.height
            ndc_x = x / (self.scale * aspect)
            ndc_y = y / self.scale

        px = (ndc_x + 1.0) * 0.5 * self.width
        py = (1.0 - ndc_y) * 0.5 * self.height
        return np.stack([px, py], axis=1), depth

    @classmethod
    def fit(
        cls,
        lower: Vec,
        upper: Vec,
        *,
        view: str | tuple[float, float, float] = "iso",
        width: int = 1024,
        height: int = 768,
        margin: float = 1.08,
        perspective: bool = False,
        fov: float = math.radians(35.0),
    ) -> Camera:
        """Frame the box *lower*..*upper* from the named or given direction."""
        lower = np.asarray(lower, dtype=float)
        upper = np.asarray(upper, dtype=float)
        centre = (lower + upper) / 2.0
        radius = float(np.linalg.norm(upper - lower)) / 2.0 or 1.0

        if isinstance(view, str):
            try:
                direction = np.array(VIEW_DIRECTIONS[view], dtype=float)
            except KeyError:
                raise ValueError(
                    f"unknown view {view!r}; expected one of " + ", ".join(sorted(VIEW_DIRECTIONS))
                ) from None
        else:
            direction = np.asarray(view, dtype=float)
        direction = _normalise(direction)

        up = _DEFAULT_UP
        if abs(float(np.dot(direction, up))) > 0.999:
            up = _ALTERNATE_UP

        distance = radius * 3.0 if not perspective else radius / math.tan(fov / 2.0) * 1.6
        camera = cls(
            eye=centre + direction * distance,
            target=centre,
            up=up,
            width=width,
            height=height,
            scale=radius * margin,
            perspective=perspective,
            fov=fov,
        )
        if not perspective:
            # A bounding *sphere* wastes most of the frame on a part that is
            # not spherical.  Scale to what the box's eight corners cover once
            # projected through this view.
            corners = np.array(
                [
                    [
                        lower[0] if i & 1 else upper[0],
                        lower[1] if i & 2 else upper[1],
                        lower[2] if i & 4 else upper[2],
                    ]
                    for i in range(8)
                ]
            )
            camera._frame(corners, margin)
        return camera

    def _frame(self, points: np.ndarray, margin: float) -> None:
        """Set :attr:`scale` so *points* just fill the frame."""
        right, view_up, _ = self.basis()
        relative = np.asarray(points, dtype=float) - self.target
        half_x = float(np.abs(relative @ right).max())
        half_y = float(np.abs(relative @ view_up).max())
        aspect = self.width / self.height
        self.scale = max(half_y, half_x / aspect, 1e-9) * margin

    @classmethod
    def fit_points(
        cls,
        points: np.ndarray,
        *,
        view: str | tuple[float, float, float] = "iso",
        width: int = 1024,
        height: int = 768,
        margin: float = 1.06,
        perspective: bool = False,
        fov: float = math.radians(35.0),
    ) -> Camera:
        """Frame the geometry itself rather than its bounding box.

        An isometric view of a box is wider than the part inside it, so fitting
        the box leaves a third of the frame empty.  Framing the actual points
        fills it.
        """
        points = np.asarray(points, dtype=float)
        if not len(points):
            raise ValueError("no points to frame")
        camera = cls.fit(
            points.min(axis=0),
            points.max(axis=0),
            view=view,
            width=width,
            height=height,
            margin=margin,
            perspective=perspective,
            fov=fov,
        )
        if not perspective:
            camera._frame(points, margin)
        return camera

    def orbit(self, angle: float) -> Camera:
        """A copy rotated *angle* radians about the target's vertical axis."""
        offset = self.eye - self.target
        axis = _normalise(_DEFAULT_UP)
        cos, sin = math.cos(angle), math.sin(angle)
        rotated = (
            offset * cos
            + np.cross(axis, offset) * sin
            + axis * float(np.dot(axis, offset)) * (1.0 - cos)
        )
        return Camera(
            eye=self.target + rotated,
            target=self.target,
            up=self.up,
            width=self.width,
            height=self.height,
            scale=self.scale,
            perspective=self.perspective,
            fov=self.fov,
        )
