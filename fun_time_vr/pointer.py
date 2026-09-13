"""The controller's ray over the scene, and what a squeeze of the trigger does to it."""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from .layout import clamp_elevation, clamp_placement, clamp_width
from .matrices import quat_to_rotation_matrix
from .scene import (
    RADIUS,
    Placement,
    center_height,
    elevation_at,
    half_width,
    surface_vertices,
    turn_deg,
)

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


def _unturned(scene_rotation: np.ndarray) -> np.ndarray:
    return np.asarray(scene_rotation, dtype=np.float64)[:3, :3].T


def _facing(rotation: np.ndarray) -> np.ndarray:
    direction = rotation @ _FORWARD
    return direction / np.linalg.norm(direction)


def _scene_pose(
    aim: AimPose, *, head: Vec3, scene_rotation: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    position, orientation = aim
    unturn = _unturned(scene_rotation)
    origin = unturn @ (np.array(position, dtype=np.float64) - np.array(head, dtype=np.float64))
    return origin, unturn @ quat_to_rotation_matrix(*orientation)


def _pointing(orientation: Quat, scene_rotation: np.ndarray) -> np.ndarray:
    return _facing(_unturned(scene_rotation) @ quat_to_rotation_matrix(*orientation))


def _bearing_deg(direction: np.ndarray) -> tuple[float, float]:
    x, y, z = direction
    return math.degrees(math.atan2(x, -z)), math.degrees(math.atan2(y, math.hypot(x, z)))


def _apart_deg(one: np.ndarray, other: np.ndarray) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(one, other))))))


def carried_heading(scene_yaw: float, carried: tuple[float, float]) -> tuple[float, float]:
    azimuth_deg, elevation_deg = carried
    return scene_yaw - math.radians(azimuth_deg), elevation_deg


def scene_ray(aim: AimPose, *, head: Vec3, scene_rotation: np.ndarray) -> Ray:
    origin, rotation = _scene_pose(aim, head=head, scene_rotation=scene_rotation)
    return Ray(origin=tuple(origin), direction=tuple(_facing(rotation)))


def held_controllers(
    hands: Mapping[str, HandInput], *, head: Vec3, scene_rotation: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray]]:
    return [_scene_pose(hand.aim, head=head, scene_rotation=scene_rotation)
            for hand in hands.values() if hand.aim is not None]


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


_EDGE_ON_DEG = 89.0


def screen_uv(
    point: SurfacePoint, placement: Placement, aspect: float, radius: float = RADIUS,
) -> tuple[float, float]:
    off_center = math.radians(max(-_EDGE_ON_DEG, min(
        _EDGE_ON_DEG, turn_deg(placement.azimuth_deg, point.azimuth_deg))))
    half = half_width(placement.width_deg, radius)
    across = radius * math.tan(off_center)
    up = point.y / math.cos(off_center) - center_height(placement, radius)
    return across / half / 2.0 + 0.5, up * aspect / half / 2.0 + 0.5


HANDLE_DEG = 4.0
MOVE = "move"
RESIZE = "resize"
SURFACE = "surface"


def handle_extent(placement: Placement, aspect: float) -> tuple[float, float]:
    du = RADIUS * math.radians(HANDLE_DEG) / (2.0 * half_width(placement.width_deg))
    return du, du * aspect


def handle_at(
    u: float, v: float, placement: Placement, *, aspect: float, resizable: bool = True,
) -> str | None:
    du, dv = handle_extent(placement, aspect)  # outside the corners, not straddling
    if resizable and -dv <= v <= 0.0 and (-du <= u <= 0.0 or 1.0 <= u <= 1.0 + du):
        return RESIZE
    if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
        return SURFACE
    if 0.0 <= u <= 1.0 and 1.0 < v <= 1.0 + dv:
        return MOVE
    return None


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
        Placement(point.azimuth_deg, elevation_at(point.y, radius), CURSOR_DEG),
        aspect=1.0, radius=radius,
    )


def handle_vertices(
    placement: Placement, *, aspect: float, resizable: bool, radius: float = RADIUS,
) -> dict[str, list[np.ndarray]]:
    du, dv = handle_extent(placement, aspect)
    strips = {MOVE: [surface_vertices(placement, aspect=aspect, radius=radius, v=(1.0, 1.0 + dv))]}
    if resizable:
        strips[RESIZE] = [
            surface_vertices(placement, aspect=aspect, radius=radius, u=across, v=(-dv, 0.0))
            for across in ((-du, 0.0), (1.0, 1.0 + du))
        ]
    return strips


class Grab:
    def __init__(
        self, handle: str, placement: Placement, *, start: SurfacePoint, aspect: float = 1.0,
        radius: float = RADIUS,
    ) -> None:
        self.handle = handle
        self._placement = placement
        self._start = start
        self._aspect = aspect
        self._radius = radius
        self._side = 1.0 if turn_deg(  # which lower corner was taken hold of
            placement.azimuth_deg, start.azimuth_deg) >= 0.0 else -1.0
        self._anchor = (  # the corner across from it, which a resize keeps still
            placement.azimuth_deg - self._side * placement.width_deg / 2.0,
            (center_height(placement, radius) + self._half_height(placement.width_deg))
            * math.cos(math.radians(placement.width_deg) / 2.0),
        )
        self._width_deg = placement.width_deg

    def _half_height(self, width_deg: float) -> float:
        return half_width(width_deg, self._radius) / self._aspect

    def _from_the_anchor(self, azimuth_deg: float) -> float:
        """How far round the grabbed corner has gone: turn_deg takes the short way,
        which reverses at a half turn, so the width already reached picks the turn."""
        turn = turn_deg(self._anchor[0], azimuth_deg)
        return turn + 360.0 * round((self._side * self._width_deg - turn) / 360.0)

    def dragged_to(self, point: SurfacePoint) -> Placement:
        placement = self._placement
        if self.handle == MOVE:
            azimuth = placement.azimuth_deg + turn_deg(self._start.azimuth_deg, point.azimuth_deg)
            off_center = math.radians(turn_deg(placement.azimuth_deg, self._start.azimuth_deg))
            lift = (center_height(placement, self._radius)
                    + (point.y - self._start.y) / math.cos(off_center))
            return clamp_placement(Placement(
                azimuth_deg=turn_deg(0.0, azimuth),
                elevation_deg=elevation_at(lift, self._radius),
                width_deg=placement.width_deg,
            ))
        return self._resized_to(point)

    def _resized_to(self, point: SurfacePoint) -> Placement:
        """Grown away from the anchored corner rather than out of the center: the
        width the pointer's reach across from it gives, and the width its reach down
        gives, weighed by how far each moves the corner."""
        azimuth, y = self._anchor
        across = math.radians(self._side * self._from_the_anchor(point.azimuth_deg))
        down = 2.0 * math.asin(
            max(-1.0, min(1.0, (y - point.y) * self._aspect / (2.0 * self._radius))))
        weight = (math.cos(math.radians(self._width_deg) / 2.0) / self._aspect) ** 2
        width_deg = clamp_width(math.degrees((across + weight * down) / (1.0 + weight)))
        self._width_deg = width_deg
        lift = y / math.cos(math.radians(width_deg) / 2.0) - self._half_height(width_deg)
        return Placement(  # not clamp_placement: its azimuth limit would slip the anchor
            azimuth_deg=turn_deg(0.0, azimuth + self._side * width_deg / 2.0),
            elevation_deg=clamp_elevation(elevation_at(lift, self._radius)),
            width_deg=width_deg,
        )


PRESS_LEVEL = 0.55
RELEASE_LEVEL = 0.35
CARRY_AFTER_DEG = 2.0
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
    stick: float = 0.0
    forward: bool = False
    back: bool = False


@dataclass(frozen=True)
class Screen:
    name: str
    placement: Placement
    aspect: float
    movable: bool = False
    resizable: bool = False
    pressable: bool = False
    immersive: bool = False  # wrapped round the viewer: no rectangle, so no hover
    picture: bool = False


@dataclass(frozen=True)
class Hover:
    screen: str
    handle: str
    u: float
    v: float


@dataclass(frozen=True)
class PressEvent:
    kind: str
    screen: str
    u: float = 0.0
    v: float = 0.0


def surface_pixel(u: float, v: float, size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    return (
        min(width - 1, max(0, int(u * width))),
        min(height - 1, max(0, int((1.0 - v) * height))),
    )


@dataclass(frozen=True)
class Frame:
    ray: Ray | None = None
    point: SurfacePoint | None = None
    hover: Hover | None = None
    moved: dict[str, Placement] = field(default_factory=dict)
    settled: bool = False
    events: tuple[PressEvent, ...] = ()
    carried: tuple[float, float] = (0.0, 0.0)
    taken: str | None = None


def _hover_at(point: SurfacePoint, screens: Sequence[Screen]) -> tuple[Screen, Hover] | None:
    for screen in reversed(screens):
        if screen.immersive:
            continue  # no rectangle to be over; see _wrapped_around below
        u, v = screen_uv(point, screen.placement, screen.aspect)
        handle = handle_at(u, v, screen.placement, aspect=screen.aspect,
                           resizable=screen.resizable)
        if handle == SURFACE or (handle is not None and screen.movable):
            return screen, Hover(screen.name, handle, u, v)
    return None


def _wrapped_around(screens: Sequence[Screen]) -> Screen | None:
    return next((screen for screen in screens
                 if screen.immersive and screen.pressable), None)


@dataclass
class _Squeeze:
    rotation: np.ndarray
    start: np.ndarray
    bearing: tuple[float, float]
    click: PressEvent | None = None
    carrying: bool = False

    @property
    def clicks(self) -> tuple[PressEvent, ...]:
        if self.carrying or self.click is None:
            return ()
        return self.click, PressEvent(RELEASE, self.click.screen)


def _nowhere(_screen: Screen, _u: float, _v: float) -> bool:
    return False


class Pointer:
    def __init__(
        self, *, on_its_controls: Callable[[Screen, float, float], bool] = _nowhere,
    ) -> None:
        self._on_its_controls = on_its_controls
        self.hand = RIGHT
        self._triggers = {LEFT: TriggerEdge(), RIGHT: TriggerEdge()}
        self._grab: tuple[Screen, Grab] | None = None
        self._pressing: Screen | None = None
        self._squeeze: _Squeeze | None = None

    def _other(self) -> str:
        return LEFT if self.hand == RIGHT else RIGHT

    @property
    def squeezing(self) -> bool:
        return any(trigger.down for trigger in self._triggers.values())

    def spend_the_squeeze(self) -> None:
        if self._squeeze is not None:
            self._squeeze.click = None

    def let_go(self) -> None:
        self._grab = None

    def frame(
        self, hands: Mapping[str, HandInput], *, head: Vec3, scene_rotation: np.ndarray,
        screens: Sequence[Screen],
    ) -> Frame:
        edges = {hand: self._triggers[hand].update(hands[hand].trigger) for hand in hands}
        if self._grab is None and self._pressing is None and self._squeeze is None:
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
        if self._squeeze is not None:
            return self._squeezing(aim, ray, point, screens, edge)
        under = _hover_at(point, screens) if point is not None else None
        if under is None:
            return self._off_every_screen(aim, ray, point, screens, edge, scene_rotation)
        screen, hover = under
        events: tuple[PressEvent, ...] = ()
        taken = screen.name if edge == PRESS else None
        if edge == PRESS and hover.handle in (MOVE, RESIZE):
            self._grab = (screen, Grab(hover.handle, screen.placement, start=point,
                                       aspect=screen.aspect))
        elif edge == PRESS and screen.picture and not self._on_its_controls(
                screen, hover.u, hover.v):
            self._start_squeeze(aim, scene_rotation, PressEvent(
                PRESS, screen.name, hover.u, hover.v) if screen.pressable else None)
        elif edge == PRESS and screen.pressable:
            self._pressing = screen
            events = (PressEvent(PRESS, screen.name, hover.u, hover.v),)
        return Frame(ray=ray, point=point, hover=hover, taken=taken, events=events)

    def _off_every_screen(self, aim: AimPose, ray: Ray, point: SurfacePoint | None,
                          screens: Sequence[Screen], edge: str | None,
                          scene_rotation: np.ndarray) -> Frame:
        if edge == PRESS:
            wrapped = _wrapped_around(screens)
            self._start_squeeze(aim, scene_rotation, None if wrapped is None else PressEvent(
                PRESS, wrapped.name, 0.5, 0.5))
        return Frame(ray=ray, point=point)

    def _start_squeeze(self, aim: AimPose, scene_rotation: np.ndarray,
                       click: PressEvent | None) -> None:
        direction = _pointing(aim[1], scene_rotation)
        self._squeeze = _Squeeze(rotation=scene_rotation, start=direction,
                                 bearing=_bearing_deg(direction), click=click)

    def _blind(self, edge: str | None) -> Frame:
        return self._let_go() if edge == RELEASE else Frame()

    def _let_go(self, **seen) -> Frame:
        grab, pressing, squeeze = self._grab, self._pressing, self._squeeze
        self._grab = self._pressing = self._squeeze = None
        if squeeze is not None:
            return Frame(settled=squeeze.carrying, events=squeeze.clicks, **seen)
        events = (PressEvent(RELEASE, pressing.name),) if pressing is not None else ()
        return Frame(settled=grab is not None, events=events, **seen)

    def _squeezing(self, aim: AimPose, ray: Ray, point: SurfacePoint | None,
                   screens: Sequence[Screen], edge: str | None) -> Frame:
        squeeze = self._squeeze
        direction = _pointing(aim[1], squeeze.rotation)
        squeeze.carrying = squeeze.carrying or _apart_deg(squeeze.start, direction) > CARRY_AFTER_DEG
        carried = (0.0, 0.0)
        if squeeze.carrying:
            bearing = _bearing_deg(direction)
            carried = (turn_deg(squeeze.bearing[0], bearing[0]), bearing[1] - squeeze.bearing[1])
            squeeze.bearing = bearing
        under = None if squeeze.carrying or point is None else _hover_at(point, screens)
        seen = dict(ray=ray, point=point, hover=None if under is None else under[1],
                    carried=carried)
        return self._let_go(**seen) if edge == RELEASE else Frame(**seen)

    def _dragging(self, ray: Ray, point: SurfacePoint | None, edge: str | None) -> Frame:
        screen, grab = self._grab
        moved = {}
        placement = screen.placement
        if point is not None:
            placement = grab.dragged_to(point)
            moved[screen.name] = placement
        u, v = screen_uv(point, placement, screen.aspect) if point is not None else (0.5, 0.5)
        seen = dict(ray=ray, point=point, hover=Hover(screen.name, grab.handle, u, v), moved=moved)
        return self._let_go(**seen) if edge == RELEASE else Frame(**seen)

    def _pressing_on(self, ray: Ray, point: SurfacePoint | None, edge: str | None) -> Frame:
        screen = self._pressing
        events: tuple[PressEvent, ...] = ()
        hover = None
        if point is not None:
            u, v = screen_uv(point, screen.placement, screen.aspect)
            hover = Hover(screen.name, SURFACE, u, v)
            events = (PressEvent(DRAG, screen.name, u, v),)
        if edge == RELEASE:
            return self._let_go(ray=ray, point=point, hover=hover)
        return Frame(ray=ray, point=point, hover=hover, events=events)
