"""The controller's ray over the scene: what it hits, and what a squeeze on it does."""
from __future__ import annotations

import math

import numpy as np
import pytest

from fun_time_vr.layout import (
    AZIMUTH_LIMIT_DEG,
    ELEVATION_LIMIT_DEG,
    MAX_WIDTH_DEG,
    MIN_WIDTH_DEG,
)
from fun_time_vr.matrices import yaw_rotation_matrix
from fun_time_vr.pointer import (
    CURSOR_DEG,
    DRAG,
    HANDLE_DEG,
    LEFT,
    MOVE,
    PRESS,
    PRESS_LEVEL,
    RELEASE,
    RELEASE_LEVEL,
    RESIZE,
    RIGHT,
    SURFACE,
    Grab,
    HandInput,
    PanelEvent,
    Pointer,
    Ray,
    Screen,
    SurfacePoint,
    TriggerEdge,
    cursor_vertices,
    cylinder_hit,
    handle_at,
    handle_extent,
    handle_vertices,
    head_position,
    laser_vertices,
    scene_ray,
    screen_uv,
)
from fun_time_vr.scene import RADIUS, Placement, scene_placement_quaternion, surface_vertices

_AHEAD = Ray(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, -1.0))


class TestTheCylinderEveryScreenHangsOn:
    def test_straight_ahead_from_the_center_lands_dead_ahead_at_the_radius(self):
        point = cylinder_hit(_AHEAD)

        assert point.azimuth_deg == pytest.approx(0.0)
        assert point.y == pytest.approx(0.0)
        assert point.distance == pytest.approx(RADIUS)

    def test_a_ray_from_the_hand_lands_where_it_points_not_where_the_head_does(self):
        # The hand sits half a meter right of the eye and points a little left,
        # so it crosses the center line before it reaches the cylinder.
        ray = Ray(origin=(0.5, -0.3, 0.0), direction=(-0.25, 0.0, -1.0))

        point = cylinder_hit(ray)

        x = 0.5 - 0.25 * point.distance / math.hypot(0.25, 1.0)
        assert point.azimuth_deg == pytest.approx(math.degrees(math.asin(x / RADIUS)), abs=1e-6)
        assert point.y == pytest.approx(-0.3)
        assert point.distance > 0

    def test_a_ray_pointed_at_the_floor_or_the_ceiling_hits_nothing(self):
        assert cylinder_hit(Ray((0.0, 0.0, 0.0), (0.0, -1.0, 0.0))) is None
        assert cylinder_hit(Ray((0.3, 0.2, 0.1), (0.0, 1.0, 0.0))) is None

    def test_from_inside_the_cylinder_the_far_wall_is_what_is_hit_whatever_the_direction(self):
        behind = cylinder_hit(Ray((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)))
        assert abs(behind.azimuth_deg) == pytest.approx(180.0)

        leftward = cylinder_hit(Ray((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0)))
        assert leftward.azimuth_deg == pytest.approx(-90.0)


_A_SCREEN = Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0)


def _on_the_cylinder(azimuth_deg: float, y: float) -> SurfacePoint:
    return SurfacePoint(azimuth_deg=azimuth_deg, y=y, distance=RADIUS)


class TestWhereOnAScreenAPointLands:
    def test_the_screens_center_is_the_middle_of_the_picture(self):
        lift = RADIUS * math.tan(math.radians(_A_SCREEN.elevation_deg))

        u, v = screen_uv(_on_the_cylinder(38.0, lift), _A_SCREEN, aspect=16 / 9)

        assert (u, v) == pytest.approx((0.5, 0.5))

    def test_u_runs_left_to_right_across_the_width(self):
        left = screen_uv(_on_the_cylinder(38.0 - 14.0, 0.0), _A_SCREEN, aspect=16 / 9)
        right = screen_uv(_on_the_cylinder(38.0 + 14.0, 0.0), _A_SCREEN, aspect=16 / 9)

        assert left[0] == pytest.approx(0.0)
        assert right[0] == pytest.approx(1.0)

    def test_v_runs_bottom_to_top_over_the_arc_height_the_mesh_uses(self):
        verts = surface_vertices(_A_SCREEN, aspect=16 / 9)
        top, bottom = verts[:, 1].max(), verts[:, 1].min()

        assert screen_uv(_on_the_cylinder(38.0, top), _A_SCREEN, aspect=16 / 9)[1] == pytest.approx(1.0)
        assert screen_uv(_on_the_cylinder(38.0, bottom), _A_SCREEN, aspect=16 / 9)[1] == pytest.approx(0.0)

    def test_past_the_edges_the_answer_keeps_going_rather_than_stopping(self):
        u, v = screen_uv(_on_the_cylinder(38.0 + 28.0, -5.0), _A_SCREEN, aspect=16 / 9)

        assert u == pytest.approx(1.5)
        assert v < 0

    def test_a_screen_hung_across_the_seam_behind_the_viewer_still_reads_whole(self):
        behind = Placement(azimuth_deg=170.0, elevation_deg=0.0, width_deg=40.0)

        u, _v = screen_uv(_on_the_cylinder(-175.0, 0.0), behind, aspect=1.0)

        assert u == pytest.approx(0.875)


_LEVEL = (0.0, 0.0, 0.0, 1.0)
_FACING_LEFT = (0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4))
_NO_TURN = np.eye(4, dtype=np.float32)


class TestTheRayInTheScene:
    def test_a_level_controller_points_forward_from_where_it_is_held(self):
        ray = scene_ray(((0.3, -0.2, -0.1), _LEVEL), head=(0.0, 0.0, 0.0), scene_rotation=_NO_TURN)

        assert ray.origin == pytest.approx((0.3, -0.2, -0.1))
        assert ray.direction == pytest.approx((0.0, 0.0, -1.0))

    def test_the_hand_is_measured_from_the_head_since_the_scene_is_drawn_around_it(self):
        ray = scene_ray(
            ((1.3, 1.4, 0.4), _LEVEL), head=(1.0, 1.6, 0.5), scene_rotation=_NO_TURN)

        assert ray.origin == pytest.approx((0.3, -0.2, -0.1))

    def test_a_recentered_scene_is_pointed_at_where_it_now_hangs(self):
        # Recentered a quarter turn left, the scene's own forward is the
        # world's left: a hand pointing left points at the primary's middle.
        turned = yaw_rotation_matrix(math.pi / 2)

        ray = scene_ray(((0.0, 0.0, 0.0), _FACING_LEFT), head=(0.0, 0.0, 0.0), scene_rotation=turned)

        assert ray.direction == pytest.approx((0.0, 0.0, -1.0), abs=1e-6)

    def test_the_hand_position_turns_with_the_scene_too(self):
        turned = yaw_rotation_matrix(math.pi / 2)

        ray = scene_ray(((-0.5, 0.0, 0.0), _LEVEL), head=(0.0, 0.0, 0.0), scene_rotation=turned)

        assert ray.origin == pytest.approx((0.0, 0.0, -0.5), abs=1e-6)

    def test_the_head_is_between_the_eyes(self):
        assert head_position([(-0.03, 1.6, 0.0), (0.03, 1.6, 0.1)]) == pytest.approx((0.0, 1.6, 0.05))


class TestTheHandles:
    """A screen's chrome: a bar to move it by, hung just above its top edge so
    it takes nothing from the picture, and two corner squares to resize it by."""

    _ASPECT = 16 / 9
    _DU = HANDLE_DEG / _A_SCREEN.width_deg
    _DV = HANDLE_DEG * _ASPECT / _A_SCREEN.width_deg

    def _at(self, u: float, v: float) -> str | None:
        return handle_at(u, v, _A_SCREEN, aspect=self._ASPECT)

    def test_the_picture_itself_is_the_surface(self):
        assert self._at(0.5, 0.5) == SURFACE
        assert self._at(0.0, 1.0) == SURFACE

    def test_the_bar_above_the_top_edge_moves_the_screen(self):
        assert self._at(0.5, 1.0 + self._DV / 2) == MOVE
        assert self._at(0.0, 1.0 + self._DV / 2) == MOVE
        assert self._at(0.5, 1.0 + self._DV * 1.5) is None

    def test_the_bottom_corners_resize_it(self):
        assert self._at(1.0 - self._DU / 4, self._DV / 4) == RESIZE
        assert self._at(1.0 + self._DU / 4, -self._DV / 4) == RESIZE
        assert self._at(-self._DU / 4, -self._DV / 4) == RESIZE
        assert self._at(0.5, -self._DV / 4) is None

    def test_a_handle_is_the_same_size_on_every_screen_whatever_its_shape(self):
        """HANDLE_DEG of arc each way, so a tall thin portrait offers the same
        target as a wide one -- and a screen far to the side is still grabbable."""
        portrait = Placement(azimuth_deg=-38.0, elevation_deg=10.0, width_deg=12.0)
        dv = HANDLE_DEG * (9 / 16) / portrait.width_deg

        assert handle_at(0.5, 1.0 + dv * 0.9, portrait, aspect=9 / 16) == MOVE
        assert handle_at(0.5, 1.0 + dv * 1.1, portrait, aspect=9 / 16) is None

    def test_a_screen_that_cannot_be_resized_offers_no_corners(self):
        assert handle_at(1.0 + self._DU / 4, -self._DV / 4, _A_SCREEN, aspect=self._ASPECT,
                         resizable=False) is None
        assert handle_at(0.5, 1.0 + self._DV / 2, _A_SCREEN, aspect=self._ASPECT,
                         resizable=False) == MOVE


def _lift(placement: Placement) -> float:
    return RADIUS * math.tan(math.radians(placement.elevation_deg))


class TestAGrab:
    """The pointer took hold of a handle; where it goes next moves or resizes
    the screen, and letting go leaves the screen there."""

    _ASPECT = 16 / 9

    def test_dragging_the_bar_sideways_turns_the_screen_with_the_pointer(self):
        grab = Grab(MOVE, _A_SCREEN, start=_on_the_cylinder(30.0, 0.9))

        moved = grab.dragged_to(_on_the_cylinder(35.0, 0.9))

        assert moved.azimuth_deg == pytest.approx(43.0)
        assert moved.elevation_deg == pytest.approx(_A_SCREEN.elevation_deg)
        assert moved.width_deg == _A_SCREEN.width_deg

    def test_dragging_the_bar_up_raises_the_screen_by_the_same_height(self):
        grab = Grab(MOVE, _A_SCREEN, start=_on_the_cylinder(38.0, 0.9))

        raised = grab.dragged_to(_on_the_cylinder(38.0, 1.4))

        assert _lift(raised) == pytest.approx(_lift(_A_SCREEN) + 0.5)
        assert raised.azimuth_deg == _A_SCREEN.azimuth_deg
        assert raised.width_deg == _A_SCREEN.width_deg

    def test_a_move_stops_at_the_scenes_limits(self):
        grab = Grab(MOVE, _A_SCREEN, start=_on_the_cylinder(38.0, 0.9))

        flung = grab.dragged_to(_on_the_cylinder(-170.0, 40.0))

        assert flung.azimuth_deg == -AZIMUTH_LIMIT_DEG
        assert flung.elevation_deg == ELEVATION_LIMIT_DEG

    def test_a_move_across_the_seam_behind_the_viewer_takes_the_short_way(self):
        behind = Placement(azimuth_deg=140.0, elevation_deg=0.0, width_deg=20.0)
        grab = Grab(MOVE, behind, start=_on_the_cylinder(175.0, 0.0))

        assert grab.dragged_to(_on_the_cylinder(-175.0, 0.0)).azimuth_deg == pytest.approx(150.0)

    def _corner(self, placement: Placement) -> SurfacePoint:
        """The bottom-right corner of *placement*, where a resize is grabbed."""
        half_arc = placement.width_deg / 2
        half_height = RADIUS * math.radians(placement.width_deg) / self._ASPECT / 2
        return _on_the_cylinder(placement.azimuth_deg + half_arc, _lift(placement) - half_height)

    def test_pulling_a_corner_twice_as_far_from_the_center_doubles_the_width(self):
        corner = self._corner(_A_SCREEN)
        grab = Grab(RESIZE, _A_SCREEN, start=corner)
        twice = _on_the_cylinder(
            _A_SCREEN.azimuth_deg + 2 * (corner.azimuth_deg - _A_SCREEN.azimuth_deg),
            _lift(_A_SCREEN) + 2 * (corner.y - _lift(_A_SCREEN)),
        )

        grown = grab.dragged_to(twice)

        assert grown.width_deg == pytest.approx(2 * _A_SCREEN.width_deg)
        assert grown.azimuth_deg == _A_SCREEN.azimuth_deg
        assert grown.elevation_deg == _A_SCREEN.elevation_deg

    def test_a_resize_stops_at_the_smallest_and_largest_a_screen_may_be(self):
        grab = Grab(RESIZE, _A_SCREEN, start=self._corner(_A_SCREEN))

        assert grab.dragged_to(_on_the_cylinder(_A_SCREEN.azimuth_deg, _lift(_A_SCREEN))
                               ).width_deg == MIN_WIDTH_DEG
        assert grab.dragged_to(_on_the_cylinder(140.0, -30.0)).width_deg == MAX_WIDTH_DEG


class TestTheTrigger:
    def test_a_squeeze_presses_once_and_a_release_releases_once(self):
        trigger = TriggerEdge()

        assert trigger.update(0.0) is None
        assert trigger.update(0.3) is None
        assert trigger.update(0.7) == PRESS
        assert trigger.update(1.0) is None
        assert trigger.down
        assert trigger.update(0.45) is None
        assert trigger.down
        assert trigger.update(0.2) == RELEASE
        assert not trigger.down
        assert trigger.update(0.1) is None

    def test_the_press_and_release_levels_leave_room_between_them(self):
        """A trigger resting right at one level would otherwise chatter."""
        assert 0.0 < RELEASE_LEVEL < PRESS_LEVEL < 1.0


def _aim_at(azimuth_deg: float, y: float) -> tuple:
    """A controller at the head, pointed at that spot on the cylinder."""
    return ((0.0, 0.0, 0.0), scene_placement_quaternion(-azimuth_deg, math.degrees(math.atan2(y, RADIUS))))


def _aim_at_uv(screen: Screen, u: float, v: float) -> tuple:
    placement = screen.placement
    half_height = RADIUS * math.radians(placement.width_deg) / screen.aspect / 2
    return _aim_at(
        placement.azimuth_deg + (u - 0.5) * placement.width_deg,
        _lift(placement) + (v - 0.5) * 2 * half_height,
    )


def _hands(right=None, left=None, *, right_trigger=0.0, left_trigger=0.0):
    return {
        RIGHT: HandInput(aim=right, trigger=right_trigger),
        LEFT: HandInput(aim=left, trigger=left_trigger),
    }


_LANDSCAPE = Screen("landscape", Placement(38.0, 10.0, 28.0), aspect=16 / 9,
                    movable=True, resizable=True)
_PANEL = Screen("panel", Placement(0.0, 32.0, 24.0), aspect=1.3, movable=True, pressable=True)
_SCENE = [_LANDSCAPE, _PANEL]


class TestThePointerOverTheScene:
    def _frame(self, pointer, hands, screens=_SCENE):
        return pointer.frame(hands, head=(0.0, 0.0, 0.0), scene_rotation=_NO_TURN, screens=screens)

    def test_with_no_controller_tracked_nothing_is_pointed_at(self):
        frame = self._frame(Pointer(), _hands())

        assert frame.ray is None
        assert frame.point is None
        assert frame.hover is None
        assert frame.moved == {}
        assert frame.events == ()

    def test_the_ray_names_the_screen_under_it_and_where_on_it(self):
        frame = self._frame(Pointer(), _hands(right=_aim_at_uv(_LANDSCAPE, 0.25, 0.75)))

        assert frame.hover.screen == "landscape"
        assert frame.hover.handle == SURFACE
        assert (frame.hover.u, frame.hover.v) == pytest.approx((0.25, 0.75), abs=1e-6)
        assert frame.point is not None
        assert frame.moved == {}

    def test_the_screen_drawn_last_is_the_one_under_the_ray_where_two_overlap(self):
        under = Screen("under", _PANEL.placement, aspect=1.3, pressable=True)

        frame = self._frame(Pointer(), _hands(right=_aim_at_uv(_PANEL, 0.5, 0.5)),
                            screens=[under, _PANEL])

        assert frame.hover.screen == "panel"

    def test_a_squeeze_on_the_bar_drags_the_screen_and_letting_go_leaves_it_there(self):
        pointer = Pointer()
        du, dv = handle_extent(_LANDSCAPE.placement, _LANDSCAPE.aspect)

        held = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 0.5, 1 + dv / 2),
                                           right_trigger=1.0))
        assert held.hover.handle == MOVE
        assert held.moved == {}

        dragged = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 0.5 + 5 / 28, 1 + dv / 2),
                                              right_trigger=1.0))
        assert dragged.moved["landscape"].azimuth_deg == pytest.approx(43.0, abs=1e-4)
        assert dragged.hover.screen == "landscape"
        assert dragged.hover.handle == MOVE
        assert not dragged.settled

        released = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 0.5 + 5 / 28, 1 + dv / 2)))
        assert released.settled
        assert released.moved["landscape"].azimuth_deg == pytest.approx(43.0, abs=1e-4)

    def test_a_squeeze_on_a_corner_resizes_the_screen(self):
        pointer = Pointer()

        held = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 1.0, 0.0), right_trigger=1.0))
        assert held.hover.handle == RESIZE

        dragged = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 1.25, -0.25), right_trigger=1.0))
        assert dragged.moved["landscape"].width_deg == pytest.approx(1.5 * 28.0)
        assert dragged.moved["landscape"].azimuth_deg == 38.0

    def test_a_squeeze_on_the_panel_presses_it_and_the_drag_and_release_follow(self):
        pointer = Pointer()

        pressed = self._frame(pointer, _hands(right=_aim_at_uv(_PANEL, 0.25, 0.75), right_trigger=1.0))
        assert len(pressed.events) == 1
        assert pressed.events[0].kind == PRESS
        assert (pressed.events[0].u, pressed.events[0].v) == pytest.approx((0.25, 0.75), abs=1e-6)

        dragged = self._frame(pointer, _hands(right=_aim_at_uv(_PANEL, 0.5, 0.75), right_trigger=1.0))
        assert dragged.events[0].kind == DRAG
        assert dragged.events[0].u == pytest.approx(0.5, abs=1e-6)
        assert dragged.hover.screen == "panel"

        released = self._frame(pointer, _hands(right=_aim_at_uv(_PANEL, 0.5, 0.75)))
        assert released.events == (PanelEvent(RELEASE),)
        assert not released.settled
        assert self._frame(pointer, _hands(right=_aim_at_uv(_PANEL, 0.5, 0.75))).events == ()

    def test_a_squeeze_on_a_satellites_picture_does_nothing(self):
        pointer = Pointer()

        pressed = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 0.5, 0.5), right_trigger=1.0))
        dragged = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 0.7, 0.5), right_trigger=1.0))

        assert pressed.hover.handle == SURFACE
        assert pressed.events == dragged.events == ()
        assert pressed.moved == dragged.moved == {}

    def test_the_hand_that_squeezes_takes_the_pointer_and_keeps_it(self):
        pointer = Pointer()
        both = dict(right=_aim_at_uv(_LANDSCAPE, 0.5, 0.5), left=_aim_at_uv(_PANEL, 0.5, 0.5))

        assert self._frame(pointer, _hands(**both)).hover.screen == "landscape"
        assert self._frame(pointer, _hands(**both, left_trigger=1.0)).hover.screen == "panel"
        assert self._frame(pointer, _hands(**both)).hover.screen == "panel"
        assert self._frame(pointer, _hands(**both, right_trigger=1.0)).hover.screen == "landscape"

    def test_a_hand_cannot_take_the_pointer_mid_press(self):
        pointer = Pointer()
        both = dict(right=_aim_at_uv(_PANEL, 0.5, 0.5), left=_aim_at_uv(_LANDSCAPE, 0.5, 0.5))

        pressed = self._frame(pointer, _hands(**both, right_trigger=1.0))
        stolen = self._frame(pointer, _hands(**both, right_trigger=1.0, left_trigger=1.0))
        released = self._frame(pointer, _hands(**both, left_trigger=1.0))

        assert pressed.events[0].kind == PRESS
        assert stolen.events[0].kind == DRAG
        assert stolen.hover.screen == "panel"
        assert released.events == (PanelEvent(RELEASE),)

    def test_the_pointer_comes_from_whichever_hand_is_tracked(self):
        frame = self._frame(Pointer(), _hands(left=_aim_at_uv(_PANEL, 0.5, 0.5)))

        assert frame.hover.screen == "panel"

    def test_a_grab_holds_through_the_ray_leaving_the_cylinder_and_the_hand_losing_tracking(self):
        pointer = Pointer()
        du, dv = handle_extent(_LANDSCAPE.placement, _LANDSCAPE.aspect)
        bar = _aim_at_uv(_LANDSCAPE, 0.5, 1 + dv / 2)
        ceiling = ((0.0, 0.0, 0.0), scene_placement_quaternion(0.0, 90.0))

        self._frame(pointer, _hands(right=bar, right_trigger=1.0))
        skyward = self._frame(pointer, _hands(right=ceiling, right_trigger=1.0))
        untracked = self._frame(pointer, _hands(right_trigger=1.0))
        back = self._frame(pointer, _hands(right=_aim_at_uv(_LANDSCAPE, 0.5 + 5 / 28, 1 + dv / 2),
                                           right_trigger=1.0))
        let_go_blind = self._frame(pointer, _hands())

        assert skyward.moved == {} and skyward.hover.handle == MOVE
        assert untracked.ray is None and untracked.moved == {}
        assert back.moved["landscape"].azimuth_deg == pytest.approx(43.0, abs=1e-4)
        assert let_go_blind.settled


def _column_azimuths(strip: np.ndarray) -> list[float]:
    return [math.degrees(math.atan2(x, -z)) for x, z in zip(strip[::2, 0], strip[::2, 2])]


class TestWhatIsDrawnForThePointer:
    def test_the_laser_is_a_ribbon_from_the_hand_along_the_ray(self):
        ray = Ray(origin=(0.3, -0.2, -0.1), direction=(0.0, 0.0, -1.0))

        strip = laser_vertices(ray, length=1.5)

        assert strip.shape == (4, 5)
        near = (strip[0, :3] + strip[1, :3]) / 2
        far = (strip[2, :3] + strip[3, :3]) / 2
        np.testing.assert_allclose(near, ray.origin, atol=1e-6)
        np.testing.assert_allclose(far, (0.3, -0.2, -1.6), atol=1e-6)

    def test_the_ribbon_lies_flat_to_the_eye_and_widens_with_distance(self):
        ray = Ray(origin=(0.3, -0.2, -0.1), direction=(-0.1, 0.0, -1.0))

        strip = laser_vertices(ray, length=2.0)

        across_near = strip[1, :3] - strip[0, :3]
        across_far = strip[3, :3] - strip[2, :3]
        assert np.dot(across_near, ray.direction) == pytest.approx(0.0, abs=1e-6)
        # Edge-on to the eye would vanish: the ribbon's width runs across the
        # line of sight, so it is at right angles to the eye's view of it.
        assert np.dot(across_near, (strip[0, :3] + strip[1, :3]) / 2) == pytest.approx(0.0, abs=1e-6)
        assert np.linalg.norm(across_far) > np.linalg.norm(across_near) > 0

    def test_a_ray_through_the_eye_still_has_a_width(self):
        strip = laser_vertices(_AHEAD, length=1.0)

        assert np.linalg.norm(strip[1, :3] - strip[0, :3]) > 0

    def test_the_cursor_is_a_small_patch_on_the_cylinder_around_the_point(self):
        strip = cursor_vertices(_on_the_cylinder(30.0, 0.7))

        azimuths = _column_azimuths(strip)
        assert (azimuths[0] + azimuths[-1]) / 2 == pytest.approx(30.0, abs=1e-4)
        assert azimuths[-1] - azimuths[0] == pytest.approx(CURSOR_DEG, abs=1e-4)
        assert (strip[:, 1].max() + strip[:, 1].min()) / 2 == pytest.approx(0.7, abs=1e-6)

    def test_the_move_bar_sits_on_the_screens_top_edge_across_its_whole_width(self):
        strips = handle_vertices(_A_SCREEN, aspect=16 / 9, resizable=True)
        screen = surface_vertices(_A_SCREEN, aspect=16 / 9)

        (bar,) = strips[MOVE]
        azimuths = _column_azimuths(bar)
        assert azimuths[0] == pytest.approx(_A_SCREEN.azimuth_deg - 14.0, abs=1e-4)
        assert azimuths[-1] == pytest.approx(_A_SCREEN.azimuth_deg + 14.0, abs=1e-4)
        assert bar[:, 1].min() == pytest.approx(screen[:, 1].max(), abs=1e-6)
        assert bar[:, 1].max() - bar[:, 1].min() == pytest.approx(
            RADIUS * math.radians(HANDLE_DEG), abs=1e-6)

    def test_the_corner_squares_straddle_the_bottom_corners(self):
        strips = handle_vertices(_A_SCREEN, aspect=16 / 9, resizable=True)
        screen = surface_vertices(_A_SCREEN, aspect=16 / 9)

        left, right = strips[RESIZE]
        assert (left[:, 1].max() + left[:, 1].min()) / 2 == pytest.approx(screen[:, 1].min(), abs=1e-6)
        left_azimuths, right_azimuths = _column_azimuths(left), _column_azimuths(right)
        assert (left_azimuths[0] + left_azimuths[-1]) / 2 == pytest.approx(
            _A_SCREEN.azimuth_deg - 14.0, abs=1e-4)
        assert (right_azimuths[0] + right_azimuths[-1]) / 2 == pytest.approx(
            _A_SCREEN.azimuth_deg + 14.0, abs=1e-4)
        assert right_azimuths[-1] - right_azimuths[0] == pytest.approx(HANDLE_DEG, abs=1e-4)

    def test_a_screen_that_cannot_be_resized_shows_no_corners(self):
        assert RESIZE not in handle_vertices(_A_SCREEN, aspect=16 / 9, resizable=False)
