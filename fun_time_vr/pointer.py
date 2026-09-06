"""The controller's ray over the scene, and what a squeeze of the trigger does to it."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from .layout import clamp_placement
from .matrices import quat_to_rotation_matrix
from .scene import RADIUS, Placement, surface_vertices

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
AimPose = tuple[Vec3, Quat]

_FORWARD = np.array([0.0, 0.0, -1.0], dtype=np.float64)


@dataclass(frozen=True)
class Ray:
    origin: Vec3
    direction: Vec3


def head_position(eye_positions: Sequence[Vec3]) -> Vec3:
    return tuple(np.mean(np.array(eye_positions, dtype=np.float64), axis=0))


def scene_ray(aim: AimPose, *, head: Vec3, scene_rotation: np.ndarray) -> Ray:
    position, orientation = aim
    unturn = np.asarray(scene_rotation, dtype=np.float64)[:3, :3].T
    origin = unturn @ (np.array(position, dtype=np.float64) - np.array(head, dtype=np.float64))
    direction = unturn @ (quat_to_rotation_matrix(*orientation) @ _FORWARD)
    direction /= np.linalg.norm(direction)
    return Ray(origin=tuple(origin), direction=tuple(direction))


@dataclass(frozen=True)
class SurfacePoint:
    azimuth_deg: float
    y: float
    distance: float


def cylinder_hit(ray: Ray, radius: float = RADIUS) -> SurfacePoint | None:
    ox, oy, oz = ray.origin
    dx, dy, dz = ray.direction
    a = dx * dx + dz * dz
    if a < 1e-12:
        return None
    b = 2.0 * (ox * dx + oz * dz)
    c = ox * ox + oz * oz - radius * radius
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return None
    t = (-b + math.sqrt(discriminant)) / (2.0 * a)
    if t <= 0.0:
        return None
    x, y, z = ox + t * dx, oy + t * dy, oz + t * dz
    return SurfacePoint(
        azimuth_deg=math.degrees(math.atan2(x, -z)),
        y=y,
        distance=t * math.sqrt(dx * dx + dy * dy + dz * dz),
    )


def _turn_deg(from_azimuth_deg: float, to_azimuth_deg: float) -> float:
    return (to_azimuth_deg - from_azimuth_deg + 180.0) % 360.0 - 180.0


def screen_uv(
    point: SurfacePoint, placement: Placement, aspect: float, radius: float = RADIUS,
) -> tuple[float, float]:
    half_height = radius * math.radians(placement.width_deg) / aspect / 2.0
    lift = radius * math.tan(math.radians(placement.elevation_deg))
    u = _turn_deg(placement.azimuth_deg, point.azimuth_deg) / placement.width_deg + 0.5
    v = (point.y - lift) / (2.0 * half_height) + 0.5
    return u, v


HANDLE_DEG = 4.0
MOVE = "move"
RESIZE = "resize"
SURFACE = "surface"


def handle_extent(placement: Placement, aspect: float) -> tuple[float, float]:
    du = HANDLE_DEG / placement.width_deg
    return du, du * aspect


def handle_at(
    u: float, v: float, placement: Placement, *, aspect: float, resizable: bool = True,
) -> str | None:
    du, dv = handle_extent(placement, aspect)
    if resizable and abs(v) <= dv / 2 and (abs(u) <= du / 2 or abs(u - 1.0) <= du / 2):
        return RESIZE
    if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
        return SURFACE
    if 0.0 <= u <= 1.0 and 1.0 < v <= 1.0 + dv:
        return MOVE
    return None


def _lift(placement: Placement, radius: float) -> float:
    return radius * math.tan(math.radians(placement.elevation_deg))


def _elevation_deg(y: float, radius: float) -> float:
    return math.degrees(math.atan2(y, radius))


CURSOR_DEG = 0.8
LASER_NEAR_HALF_WIDTH = 0.002
LASER_FAR_HALF_WIDTH = 0.006


def laser_vertices(ray: Ray, *, length: float) -> np.ndarray:
    origin = np.array(ray.origin, dtype=np.float64)
    direction = np.array(ray.direction, dtype=np.float64)
    far = origin + direction * length
    across = np.cross(direction, (origin + far) / 2.0)
    if np.linalg.norm(across) < 1e-9:
        across = np.cross(direction, (0.0, 1.0, 0.0))
    if np.linalg.norm(across) < 1e-9:
        across = np.cross(direction, (1.0, 0.0, 0.0))
    across /= np.linalg.norm(across)
    rows = [
        (*(origin - across * LASER_NEAR_HALF_WIDTH), 0.0, 0.0),
        (*(origin + across * LASER_NEAR_HALF_WIDTH), 0.0, 1.0),
        (*(far - across * LASER_FAR_HALF_WIDTH), 1.0, 0.0),
        (*(far + across * LASER_FAR_HALF_WIDTH), 1.0, 1.0),
    ]
    return np.array(rows, dtype=np.float32)


def cursor_vertices(point: SurfacePoint, radius: float = RADIUS) -> np.ndarray:
    return surface_vertices(
        Placement(point.azimuth_deg, _elevation_deg(point.y, radius), CURSOR_DEG),
        aspect=1.0, radius=radius, segments=2,
    )


def handle_vertices(
    placement: Placement, *, aspect: float, resizable: bool, radius: float = RADIUS,
) -> dict[str, list[np.ndarray]]:
    width_rad = math.radians(placement.width_deg)
    half_height = radius * width_rad / aspect / 2.0
    lift = _lift(placement, radius)
    handle = radius * math.radians(HANDLE_DEG)
    bar = Placement(
        placement.azimuth_deg,
        _elevation_deg(lift + half_height + handle / 2.0, radius),
        placement.width_deg,
    )
    strips = {MOVE: [surface_vertices(bar, aspect=radius * width_rad / handle, radius=radius)]}
    if resizable:
        bottom = _elevation_deg(lift - half_height, radius)
        strips[RESIZE] = [
            surface_vertices(
                Placement(placement.azimuth_deg + side * placement.width_deg / 2.0, bottom,
                          HANDLE_DEG),
                aspect=1.0, radius=radius, segments=2,
            )
            for side in (-1.0, 1.0)
        ]
    return strips


class Grab:
    def __init__(
        self, handle: str, placement: Placement, *, start: SurfacePoint, radius: float = RADIUS,
    ) -> None:
        self.handle = handle
        self._placement = placement
        self._start = start
        self._radius = radius
        self._start_reach = self._reach(start)

    def _reach(self, point: SurfacePoint) -> float:
        along = self._radius * math.radians(
            _turn_deg(self._placement.azimuth_deg, point.azimuth_deg))
        return math.hypot(along, point.y - _lift(self._placement, self._radius))

    def dragged_to(self, point: SurfacePoint) -> Placement:
        placement = self._placement
        if self.handle == MOVE:
            azimuth = placement.azimuth_deg + _turn_deg(self._start.azimuth_deg, point.azimuth_deg)
            lift = _lift(placement, self._radius) + point.y - self._start.y
            return clamp_placement(Placement(
                azimuth_deg=_turn_deg(0.0, azimuth),
                elevation_deg=math.degrees(math.atan2(lift, self._radius)),
                width_deg=placement.width_deg,
            ))
        scale = self._reach(point) / self._start_reach if self._start_reach > 0.0 else 1.0
        return clamp_placement(replace(placement, width_deg=placement.width_deg * scale))


PRESS_LEVEL = 0.55
RELEASE_LEVEL = 0.35
PRESS = "press"
RELEASE = "release"


class TriggerEdge:
    def __init__(self) -> None:
        self.down = False

    def update(self, value: float) -> str | None:
        if not self.down and value >= PRESS_LEVEL:
            self.down = True
            return PRESS
        if self.down and value <= RELEASE_LEVEL:
            self.down = False
            return RELEASE
        return None


LEFT = "left"
RIGHT = "right"
DRAG = "drag"


@dataclass(frozen=True)
class HandInput:
    aim: AimPose | None = None
    trigger: float = 0.0


@dataclass(frozen=True)
class Screen:
    name: str
    placement: Placement
    aspect: float
    movable: bool = False
    resizable: bool = False
    pressable: bool = False


@dataclass(frozen=True)
class Hover:
    screen: str
    handle: str
    u: float
    v: float


@dataclass(frozen=True)
class PanelEvent:
    kind: str
    u: float = 0.0
    v: float = 0.0


@dataclass(frozen=True)
class Frame:
    ray: Ray | None = None
    point: SurfacePoint | None = None
    hover: Hover | None = None
    moved: dict[str, Placement] = field(default_factory=dict)
    settled: bool = False
    events: tuple[PanelEvent, ...] = ()


def _hover_at(point: SurfacePoint, screens: Sequence[Screen]) -> tuple[Screen, Hover] | None:
    for screen in reversed(screens):
        u, v = screen_uv(point, screen.placement, screen.aspect)
        handle = handle_at(u, v, screen.placement, aspect=screen.aspect,
                           resizable=screen.resizable)
        if handle == SURFACE or (handle is not None and screen.movable):
            return screen, Hover(screen.name, handle, u, v)
    return None


class Pointer:
    def __init__(self) -> None:
        self.hand = RIGHT
        self._triggers = {LEFT: TriggerEdge(), RIGHT: TriggerEdge()}
        self._grab: tuple[Screen, Grab] | None = None
        self._pressing: Screen | None = None

    def _other(self) -> str:
        return LEFT if self.hand == RIGHT else RIGHT

    def frame(
        self, hands: Mapping[str, HandInput], *, head: Vec3, scene_rotation: np.ndarray,
        screens: Sequence[Screen],
    ) -> Frame:
        edges = {hand: self._triggers[hand].update(hands[hand].trigger) for hand in hands}
        if self._grab is None and self._pressing is None:
            other = self._other()
            if edges.get(other) == PRESS or (
                    hands[self.hand].aim is None and hands[other].aim is not None):
                self.hand = other
        edge = edges[self.hand]
        aim = hands[self.hand].aim
        if aim is None:
            return self._blind(edge)
        ray = scene_ray(aim, head=head, scene_rotation=scene_rotation)
        point = cylinder_hit(ray)
        if self._grab is not None:
            return self._dragging(ray, point, edge)
        if self._pressing is not None:
            return self._pressing_on(ray, point, edge)
        under = _hover_at(point, screens) if point is not None else None
        if under is None:
            return Frame(ray=ray, point=point)
        screen, hover = under
        if edge == PRESS and hover.handle in (MOVE, RESIZE):
            self._grab = (screen, Grab(hover.handle, screen.placement, start=point))
        elif edge == PRESS and screen.pressable:
            self._pressing = screen
            return Frame(ray=ray, point=point, hover=hover,
                         events=(PanelEvent(PRESS, hover.u, hover.v),))
        return Frame(ray=ray, point=point, hover=hover)

    def _blind(self, edge: str | None) -> Frame:
        if edge != RELEASE:
            return Frame()
        settled = self._grab is not None
        events = (PanelEvent(RELEASE),) if self._pressing is not None else ()
        self._grab = self._pressing = None
        return Frame(settled=settled, events=events)

    def _dragging(self, ray: Ray, point: SurfacePoint | None, edge: str | None) -> Frame:
        screen, grab = self._grab
        moved = {}
        placement = screen.placement
        if point is not None:
            placement = grab.dragged_to(point)
            moved[screen.name] = placement
        u, v = screen_uv(point, placement, screen.aspect) if point is not None else (0.5, 0.5)
        hover = Hover(screen.name, grab.handle, u, v)
        if edge == RELEASE:
            self._grab = None
            return Frame(ray=ray, point=point, hover=hover, moved=moved, settled=True)
        return Frame(ray=ray, point=point, hover=hover, moved=moved)

    def _pressing_on(self, ray: Ray, point: SurfacePoint | None, edge: str | None) -> Frame:
        screen = self._pressing
        events: tuple[PanelEvent, ...] = ()
        hover = None
        if point is not None:
            u, v = screen_uv(point, screen.placement, screen.aspect)
            hover = Hover(screen.name, SURFACE, u, v)
            events = (PanelEvent(DRAG, u, v),)
        if edge == RELEASE:
            self._pressing = None
            events = (PanelEvent(RELEASE),)
        return Frame(ray=ray, point=point, hover=hover, events=events)
