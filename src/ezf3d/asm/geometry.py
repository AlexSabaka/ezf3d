"""Analytic curves and surfaces from ASM records.

Every geometry record opens with the same three fields (attrib pointer, an int,
a second pointer) and then its own layout.  The trailing region is ACIS's
optional parameter box, written as ``(bool, [f64])`` pairs: ``BOOL_TRUE`` alone
means unbounded, ``BOOL_FALSE`` is followed by the bound.

Fusion's kernel works in centimetres, so every length here is cm.

Curve parameterisation is verified rather than assumed: evaluating a curve at
its edge's stored parameter must land on the vertex point, and for ordinary
(non-tolerant) topology it does so to within the file's own ``resabs`` --
2.2e-07 cm worst case over 102,237 endpoints across the sample designs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ezf3d.asm.records import Entity
from ezf3d.asm.tokens import Tag

Vec = np.ndarray


class GeometryError(ValueError):
    """Raised for a geometry record ezf3d cannot interpret."""


def _unpack(entity: Entity) -> tuple[list[Vec], list[Vec], list[float], list[bool]]:
    """Split a geometry record's fields into positions, directions, reals, flags."""
    positions: list[Vec] = []
    directions: list[Vec] = []
    reals: list[float] = []
    flags: list[bool] = []
    for tag, value in entity.fields:
        if tag == Tag.POSITION:
            positions.append(np.array(value, dtype=float))
        elif tag == Tag.DIRECTION:
            directions.append(np.array(value, dtype=float))
        elif tag == Tag.DOUBLE:
            reals.append(float(value))  # type: ignore[arg-type]
        elif tag == Tag.BOOL_TRUE:
            flags.append(True)
        elif tag == Tag.BOOL_FALSE:
            flags.append(False)
    return positions, directions, reals, flags


def _normalise(vector: Vec) -> Vec:
    length = float(np.linalg.norm(vector))
    if length == 0.0:
        raise GeometryError("zero-length direction")
    return vector / length


# -- curves ----------------------------------------------------------------


@dataclass(slots=True)
class Curve:
    """Base class for parametric curves."""

    def point_at(self, t: float) -> Vec:  # pragma: no cover - interface
        raise NotImplementedError

    def points_at(self, ts: np.ndarray) -> np.ndarray:
        """Evaluate many parameters at once."""
        return np.array([self.point_at(float(t)) for t in ts])

    def invert(self, point: Vec) -> float:
        """Parameter of the point on this curve nearest *point*.

        ASM edges sometimes store a sentinel parameter range — an edge one
        centimetre long carrying a range of +/-100 — so the vertices, not the
        stored range, are what pins an edge's extent.  Inverting at the
        vertices is how a discretiser recovers the real parameters.
        """
        raise NotImplementedError  # pragma: no cover - interface

    def distance_to(self, point: Vec) -> float:
        """Distance from *point* to this curve.  Convention-free."""
        return float(np.linalg.norm(point - self.point_at(self.invert(point))))

    @property
    def is_periodic(self) -> bool:
        return False

    @property
    def period(self) -> float | None:
        return None


@dataclass(slots=True)
class Straight(Curve):
    """An infinite line.  The parameter is signed distance along *direction*."""

    root: Vec
    direction: Vec

    def point_at(self, t: float) -> Vec:
        return self.root + t * self.direction

    def points_at(self, ts: np.ndarray) -> np.ndarray:
        return self.root + ts[:, None] * self.direction

    def invert(self, point: Vec) -> float:
        return float(np.dot(point - self.root, self.direction))


@dataclass(slots=True)
class Ellipse(Curve):
    """A circle or ellipse.  The parameter is the angle in radians.

    *major* is a vector whose length is the major radius; *ratio* scales it to
    the minor radius.  ``ratio == 1`` is a circle.
    """

    centre: Vec
    normal: Vec
    major: Vec
    ratio: float

    @property
    def minor(self) -> Vec:
        return np.cross(self.normal, self.major) * self.ratio

    @property
    def major_radius(self) -> float:
        return float(np.linalg.norm(self.major))

    @property
    def minor_radius(self) -> float:
        return self.major_radius * self.ratio

    def point_at(self, t: float) -> Vec:
        return self.centre + self.major * math.cos(t) + self.minor * math.sin(t)

    def points_at(self, ts: np.ndarray) -> np.ndarray:
        return self.centre + np.cos(ts)[:, None] * self.major + np.sin(ts)[:, None] * self.minor

    def invert(self, point: Vec) -> float:
        """Angle of *point* about the ellipse, in ``[-pi, pi]``.

        Measured in the frame the ellipse defines, so a non-circular ellipse
        inverts to the parameter that generated the point rather than to the
        geometric angle.
        """
        delta = point - self.centre
        major_radius = self.major_radius
        minor_radius = self.minor_radius
        if major_radius == 0.0 or minor_radius == 0.0:
            raise GeometryError("degenerate ellipse")
        x = float(np.dot(delta, self.major)) / (major_radius * major_radius)
        y = float(np.dot(delta, self.minor)) / (minor_radius * minor_radius)
        return math.atan2(y, x)

    @property
    def is_periodic(self) -> bool:
        return True

    @property
    def period(self) -> float:
        return 2.0 * math.pi


# -- surfaces --------------------------------------------------------------


@dataclass(slots=True)
class Surface:
    """Base class for parametric surfaces."""

    def point_at(self, u: float, v: float) -> Vec:  # pragma: no cover - interface
        raise NotImplementedError

    def points_at(self, uv: np.ndarray) -> np.ndarray:
        """Evaluate an ``(n, 2)`` array of parameters."""
        return np.array([self.point_at(float(u), float(v)) for u, v in uv])

    def invert(self, point: Vec) -> tuple[float, float]:
        """Parameters of the point on this surface nearest *point*.

        This is what makes trimming possible: a face's boundary is given as 3D
        curves, and triangulating it needs those curves in the surface's own
        parameter space.
        """
        raise NotImplementedError  # pragma: no cover - interface

    def normal_at(self, u: float, v: float) -> Vec:
        """Unit normal at ``(u, v)``, by central difference if not overridden."""
        step = 1e-6
        du = self.point_at(u + step, v) - self.point_at(u - step, v)
        dv = self.point_at(u, v + step) - self.point_at(u, v - step)
        normal = np.cross(du, dv)
        length = float(np.linalg.norm(normal))
        if length < 1e-30:
            return np.array([0.0, 0.0, 1.0])
        return normal / length

    def distance_to(self, point: Vec) -> float:
        """Unsigned distance from *point* to the surface.

        Used to check a face's vertices really lie on its surface without
        needing the UV convention to be settled first.
        """
        raise NotImplementedError  # pragma: no cover - interface

    @property
    def u_period(self) -> float | None:
        """Period of the *u* parameter, or ``None`` if it does not wrap."""
        return None

    @property
    def v_period(self) -> float | None:
        return None

    @property
    def v_radius(self) -> float | None:
        """Radius of curvature along *v*, or ``None`` where *v* is straight.

        A tessellator needs this to know whether a strip spanning *v* has to be
        subdivided: a cylinder is ruled along *v* and needs nothing, while a
        fillet torus curves through its whole tube.
        """
        return None


@dataclass(slots=True)
class Plane(Surface):
    origin: Vec
    normal: Vec
    u_dir: Vec

    @property
    def v_dir(self) -> Vec:
        return np.cross(self.normal, self.u_dir)

    def point_at(self, u: float, v: float) -> Vec:
        return self.origin + u * self.u_dir + v * self.v_dir

    def points_at(self, uv: np.ndarray) -> np.ndarray:
        uv = np.asarray(uv, dtype=float)
        return self.origin + uv[:, 0:1] * self.u_dir + uv[:, 1:2] * self.v_dir

    def invert(self, point: Vec) -> tuple[float, float]:
        delta = point - self.origin
        return float(np.dot(delta, self.u_dir)), float(np.dot(delta, self.v_dir))

    def normal_at(self, u: float, v: float) -> Vec:
        return self.normal

    def distance_to(self, point: Vec) -> float:
        return abs(float(np.dot(point - self.origin, self.normal)))


@dataclass(slots=True)
class Cone(Surface):
    """A cone or, when the half-angle is zero, a cylinder.

    ASM writes both with the same record: *major* is the radius vector at
    *base* and ``sin_angle``/``cos_angle`` give the half-angle of the taper, so
    a cylinder is simply ``sin_angle == 0``.

    ``ratio`` scales the minor axis: it is 1 for the circular case and less for
    an **elliptical** cone, which Fusion does emit — 884 of the sample designs'
    cone faces have ``ratio == 0.7678``.  The cross-section is then an ellipse,
    and a circular radius test would be wrong by up to a millimetre.
    """

    base: Vec
    axis: Vec
    major: Vec
    ratio: float
    sin_angle: float
    cos_angle: float

    @property
    def base_radius(self) -> float:
        """Major semi-axis of the cross-section at *base*."""
        return float(np.linalg.norm(self.major))

    @property
    def minor(self) -> Vec:
        return np.cross(self.axis, self.major) * self.ratio

    @property
    def is_cylinder(self) -> bool:
        return abs(self.sin_angle) < 1e-12

    @property
    def is_circular(self) -> bool:
        return abs(self.ratio - 1.0) < 1e-12

    @property
    def half_angle(self) -> float:
        return math.atan2(self.sin_angle, self.cos_angle)

    def scale_at_height(self, h: float) -> float:
        """How much the cross-section grows *h* along the axis from *base*."""
        if self.is_cylinder:
            return 1.0
        taper = self.sin_angle / self.cos_angle
        return 1.0 + h * taper / self.base_radius

    def point_at(self, u: float, v: float) -> Vec:
        """*u* is the angle around the axis, *v* the height along it."""
        radial = math.cos(u) * self.major + math.sin(u) * self.minor
        return self.base + v * self.axis + self.scale_at_height(v) * radial

    def invert(self, point: Vec) -> tuple[float, float]:
        delta = point - self.base
        height = float(np.dot(delta, self.axis))
        radial = delta - height * self.axis
        scale = self.scale_at_height(height)
        major_len = self.base_radius or 1.0
        major_dir = self.major / major_len
        minor_dir = np.cross(self.axis, major_dir)
        x = float(np.dot(radial, major_dir)) / max(major_len * scale, 1e-30)
        y = float(np.dot(radial, minor_dir)) / max(major_len * self.ratio * scale, 1e-30)
        return math.atan2(y, x), height

    @property
    def u_period(self) -> float:
        return 2.0 * math.pi

    def distance_to(self, point: Vec) -> float:
        """Radial distance from *point* to the surface.

        Exact for a circular cone; for an elliptical one this measures along
        the ray from the axis, which is zero exactly on the surface and a close
        approximation elsewhere — a true Euclidean distance to an ellipse has
        no closed form.
        """
        delta = point - self.base
        height = float(np.dot(delta, self.axis))
        radial_vec = delta - height * self.axis
        radius = float(np.linalg.norm(radial_vec))
        scale = self.scale_at_height(height)

        semi_major = self.base_radius * scale
        if semi_major <= 0.0:
            return radius  # past the apex
        if self.is_circular:
            return abs(radius - semi_major)

        semi_minor = semi_major * self.ratio
        if radius == 0.0:
            return min(semi_major, semi_minor)
        # Radius of the cross-section ellipse along this point's own direction.
        major_dir = self.major / self.base_radius
        minor_dir = np.cross(self.axis, major_dir)
        angle = math.atan2(
            float(np.dot(radial_vec, minor_dir)), float(np.dot(radial_vec, major_dir))
        )
        on_ellipse = (semi_major * semi_minor) / math.hypot(
            semi_minor * math.cos(angle), semi_major * math.sin(angle)
        )
        return abs(radius - on_ellipse)


@dataclass(slots=True)
class Sphere(Surface):
    centre: Vec
    radius: float
    pole: Vec
    u_ref: Vec

    def point_at(self, u: float, v: float) -> Vec:
        w = np.cross(self.pole, self.u_ref)
        return self.centre + self.radius * (
            math.cos(v) * (math.cos(u) * self.u_ref + math.sin(u) * w) + math.sin(v) * self.pole
        )

    @property
    def v_radius(self) -> float:
        return abs(self.radius)

    def invert(self, point: Vec) -> tuple[float, float]:
        """*u* is longitude about the pole, *v* latitude from the equator."""
        delta = point - self.centre
        w = np.cross(self.pole, self.u_ref)
        height = float(np.dot(delta, self.pole))
        x = float(np.dot(delta, self.u_ref))
        y = float(np.dot(delta, w))
        return math.atan2(y, x), math.atan2(height, math.hypot(x, y))

    @property
    def u_period(self) -> float:
        return 2.0 * math.pi

    def distance_to(self, point: Vec) -> float:
        return abs(float(np.linalg.norm(point - self.centre)) - abs(self.radius))


@dataclass(slots=True)
class Torus(Surface):
    centre: Vec
    axis: Vec
    major_radius: float
    minor_radius: float
    u_ref: Vec

    def point_at(self, u: float, v: float) -> Vec:
        w = np.cross(self.axis, self.u_ref)
        radial = math.cos(u) * self.u_ref + math.sin(u) * w
        return (
            self.centre
            + (self.major_radius + self.minor_radius * math.cos(v)) * radial
            + self.minor_radius * math.sin(v) * self.axis
        )

    @property
    def v_radius(self) -> float:
        return abs(self.minor_radius)

    def invert(self, point: Vec) -> tuple[float, float]:
        """*u* runs around the ring, *v* around the tube.

        The minor radius is **signed**: ASM writes it negative for a concave
        fillet, where the tube is subtracted rather than added.  Dividing the
        tube-frame coordinates by it puts *v* back on the right side — reading
        it as positive puts every concave fillet half a tube out of place.
        """
        delta = point - self.centre
        w = np.cross(self.axis, self.u_ref)
        height = float(np.dot(delta, self.axis))
        x = float(np.dot(delta, self.u_ref))
        y = float(np.dot(delta, w))
        minor = self.minor_radius or 1.0
        radial = math.hypot(x, y) - self.major_radius
        return math.atan2(y, x), math.atan2(height / minor, radial / minor)

    @property
    def u_period(self) -> float:
        return 2.0 * math.pi

    @property
    def v_period(self) -> float:
        return 2.0 * math.pi

    def distance_to(self, point: Vec) -> float:
        delta = point - self.centre
        height = float(np.dot(delta, self.axis))
        radial = float(np.linalg.norm(delta - height * self.axis))
        return abs(math.hypot(radial - abs(self.major_radius), height) - abs(self.minor_radius))


@dataclass(slots=True)
class SplineSurface(Surface):
    """Placeholder for a spline surface, which Phase 2.4 will evaluate.

    Carried rather than dropped so a face backed by one can be reported as
    unsupported instead of silently vanishing.
    """

    entity: Entity

    def distance_to(self, point: Vec) -> float:
        raise GeometryError("spline surface evaluation is not implemented yet")


@dataclass(slots=True)
class SplineCurve(Curve):
    """Placeholder for an ``intcurve`` / ``pcurve``; see :class:`SplineSurface`."""

    entity: Entity

    def point_at(self, t: float) -> Vec:
        raise GeometryError("spline curve evaluation is not implemented yet")


# -- readers ---------------------------------------------------------------

#: Concrete classes this module can evaluate today.
ANALYTIC_CURVE_NAMES = frozenset({"straight", "ellipse"})
ANALYTIC_SURFACE_NAMES = frozenset({"plane", "cone", "sphere", "torus"})


def read_curve(entity: Entity) -> Curve:
    """Build a :class:`Curve` from a ``curve``-based entity."""
    positions, directions, reals, _ = _unpack(entity)
    name = entity.name
    if name == "straight":
        return Straight(root=positions[0], direction=_normalise(directions[0]))
    if name == "ellipse":
        return Ellipse(
            centre=positions[0],
            normal=_normalise(directions[0]),
            major=directions[1],
            ratio=reals[0],
        )
    if name in ("intcurve", "pcurve"):
        return SplineCurve(entity=entity)
    raise GeometryError(f"unsupported curve class {name!r}")


def read_surface(entity: Entity) -> Surface:
    """Build a :class:`Surface` from a ``surface``-based entity."""
    positions, directions, reals, _ = _unpack(entity)
    name = entity.name
    if name == "plane":
        return Plane(
            origin=positions[0],
            normal=_normalise(directions[0]),
            u_dir=_normalise(directions[1]),
        )
    if name == "cone":
        return Cone(
            base=positions[0],
            axis=_normalise(directions[0]),
            major=directions[1],
            ratio=reals[0],
            sin_angle=reals[1],
            cos_angle=reals[2],
        )
    if name == "sphere":
        return Sphere(
            centre=positions[0],
            radius=reals[0],
            pole=_normalise(directions[0]),
            u_ref=_normalise(directions[1]),
        )
    if name == "torus":
        return Torus(
            centre=positions[0],
            axis=_normalise(directions[0]),
            major_radius=reals[0],
            minor_radius=reals[1],
            u_ref=_normalise(directions[1]),
        )
    if name == "spline":
        return SplineSurface(entity=entity)
    raise GeometryError(f"unsupported surface class {name!r}")
