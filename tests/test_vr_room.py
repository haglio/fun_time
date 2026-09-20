"""What the headset's room holds, gathered from the things that hang it there."""
from __future__ import annotations

from fun_time_vr import room
from fun_time_vr.pointer import Screen
from fun_time_vr.room import Hanging
from fun_time_vr.scene import Placement
from fun_time_vr.stacking import Stacking

SOMEWHERE = Placement(azimuth_deg=0.0, elevation_deg=0.0, width_deg=40.0)


def _screen(name: str, **flags) -> Screen:
    return Screen(name, SOMEWHERE, 16 / 9, **flags)


class _Thing:
    """Something in the room, hanging whatever it was given."""

    def __init__(self, *hangings: Hanging) -> None:
        self._hangings = hangings

    def hangings(self) -> tuple[Hanging, ...]:
        return self._hangings

    def hangs_by(self) -> dict[str, room.Hangs]:
        return {}


def _arranged(*things, stacking=None) -> list[str]:
    return [screen.name for screen in
            room.arranged(stacking or Stacking(), room.what_hangs(things))]


class TestWhatThePointerIsOffered:
    def test_a_screen_a_thing_hangs_is_offered(self):
        assert _arranged(_Thing(Hanging(_screen("notes", pressable=True)))) == ["notes"]

    def test_each_screen_stands_on_its_own_unless_it_says_otherwise(self):
        stacking = Stacking()
        stacking.take("notes")

        assert _arranged(_Thing(Hanging(_screen("notes")), Hanging(_screen("clock"))),
                         stacking=stacking) == ["clock", "notes"]

    def test_a_screen_that_hangs_with_another_comes_forward_with_it(self):
        stacking = Stacking()
        stacking.take("notes")

        assert _arranged(
            _Thing(Hanging(_screen("clock")), Hanging(_screen("alarm"), forward_with="clock")),
            _Thing(Hanging(_screen("notes"))), stacking=stacking) == ["clock", "alarm", "notes"]

    def test_a_screen_docked_to_another_comes_forward_when_that_one_does(self):
        """It rides on what is above it, without being part of it."""
        stacking = Stacking()
        stacking.take("clock")

        assert _arranged(_Thing(Hanging(_screen("notes"))),
                         _Thing(Hanging(_screen("shelf"), docked_to="clock")),
                         _Thing(Hanging(_screen("clock"))), stacking=stacking) == [
            "notes", "shelf", "clock"]

    def test_a_screen_put_in_front_is_over_whatever_was_taken_last(self):
        stacking = Stacking()
        stacking.take("notes")

        assert _arranged(_Thing(Hanging(_screen("browse"), in_front=True)),
                         _Thing(Hanging(_screen("notes"))), stacking=stacking) == [
            "notes", "browse"]


class _Quad:
    def __init__(self, name: str, *, ready: bool = True) -> None:
        self.mesh, self.ready = name, ready


class _Picture:
    def __init__(self, name: str) -> None:
        self.texture = name


def _hanging(name: str, *, ready: bool = True, blend: bool = False, **rest) -> Hanging:
    return Hanging(_screen(name, **{k: v for k, v in rest.items() if k in _SCREEN_FLAGS}),
                mesh=_Quad(name, ready=ready), picture=_Picture(name), blend=blend,
                **{k: v for k, v in rest.items() if k not in _SCREEN_FLAGS})


_SCREEN_FLAGS = ("movable", "resizable", "pressable", "immersive", "picture")


class TestWhatReachesTheEyes:
    def _drawn(self, *hangings: Hanging, as_quads=()):
        return room.drawn(hangings, screens=[one.screen for one in hangings], as_quads=set(as_quads))

    def test_each_screen_is_drawn_with_its_own_picture(self):
        assert self._drawn(_hanging("notes"), _hanging("clock", blend=True)) == [
            ("notes", "notes", False), ("clock", "clock", True)]

    def test_they_are_drawn_in_the_order_they_stand(self):
        one, other = _hanging("notes"), _hanging("clock")

        drawn = room.drawn([one, other], screens=[other.screen, one.screen], as_quads=set())

        assert [mesh for mesh, _picture, _blend in drawn] == ["clock", "notes"]

    def test_a_screen_the_compositor_took_as_a_quad_is_left_out(self):
        """The runtime composites those over this layer, so drawing it here
        would show through whatever the quad is under."""
        assert self._drawn(_hanging("notes"), _hanging("clock"), as_quads=("notes",)) == [
            ("clock", "clock", False)]

    def test_a_screen_with_no_quad_yet_is_left_out(self):
        assert self._drawn(_hanging("notes", ready=False)) == []

    def test_a_picture_round_the_viewer_is_no_part_of_the_flat_pass(self):
        wrap = Hanging(_screen("main", immersive=True), picture=_Picture("main"), wrap=7)

        assert self._drawn(wrap) == []

    def test_the_one_round_the_viewer_is_found_by_itself(self):
        wrap = Hanging(_screen("main", immersive=True), picture=_Picture("main"), wrap=7)

        assert room.wrapped([_hanging("clock"), wrap]) is wrap

    def test_nothing_is_round_the_viewer_while_every_screen_is_flat(self):
        assert room.wrapped([_hanging("clock")]) is None


class _Hanger:
    """Something in the room that hangs nothing, but is moved by a drag."""

    def __init__(self, hangs) -> None:
        self._hangs = hangs

    def hangings(self) -> tuple[Hanging, ...]:
        return ()

    def hangs_by(self):
        return self._hangs


class _Moved:
    def __init__(self, placement) -> None:
        self.placement = placement


ELSEWHERE = Placement(azimuth_deg=12.0, elevation_deg=-3.0, width_deg=30.0)


class TestWhereEachScreenHangs:
    def test_a_drag_moves_what_the_thing_says_it_moves(self):
        moved = _Moved(SOMEWHERE)

        room.where_they_hang([_Hanger({"notes": room.Hangs((moved,))})])["notes"].put(ELSEWHERE)

        assert moved.placement == ELSEWHERE

    def test_two_things_hanging_by_one_name_both_move(self):
        """The main slot is two players' screens, whichever is showing."""
        one, other = _Moved(SOMEWHERE), _Moved(SOMEWHERE)

        where = room.where_they_hang([_Hanger({"main": room.Hangs((one,))}),
                                      _Hanger({"main": room.Hangs((other,))})])
        where["main"].put(ELSEWHERE)

        assert (one.placement, other.placement) == (ELSEWHERE, ELSEWHERE)

    def test_where_it_hangs_now_is_readable_without_a_drag(self):
        where = room.where_they_hang([_Hanger({"notes": room.Hangs((_Moved(SOMEWHERE),))})])

        assert where["notes"].placement == SOMEWHERE

    def test_a_drag_is_remembered_in_the_screens_own_spot(self):
        where = room.where_they_hang([_Hanger({"notes": room.Hangs((_Moved(SOMEWHERE),))})])

        assert where["notes"].spot("notes") == "notes"

    def test_a_screen_with_two_spots_says_which_one_the_drag_landed_in(self):
        """The dashboard keeps one spot for each of the places it hangs."""
        hangs = room.Hangs((_Moved(SOMEWHERE),), kept_as=lambda: "wrapped")

        assert room.where_they_hang([_Hanger({"dash": hangs})])["dash"].spot("dash") == "wrapped"


class TestTheGatesOnTheRoomsAssembly:
    """Two branches that each added a screen used to conflict on six places at
    once — both assembly functions, both call sites, the frame loop's own map
    and the loop's construction — because every screen was threaded through the
    loop by hand.  These hold that shut: the assembly knows nothing about what
    kinds of screen there are, and a screen is registered in one place.
    """

    def _source(self, thing) -> str:
        import inspect

        return inspect.getsource(thing)

    def _names_in(self, source: str) -> set[str]:
        import ast

        return {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}

    def _kinds_of_screen(self) -> set[str]:
        from fun_time_vr import player

        return {name for name, thing in vars(player).items()
                if isinstance(thing, type) and name.endswith("Unit")}

    def _kinds_the_room_holds(self) -> set[str]:
        """The kinds registered, which is every one of them but the shared base."""
        from fun_time_vr import player

        kinds = {name: getattr(player, name) for name in self._kinds_of_screen()}
        return {name for name, kind in kinds.items()
                if not any(other is not kind and issubclass(other, kind)
                           for other in kinds.values())}

    def _screen_names(self) -> set[str]:
        from fun_time_vr import layout

        return {name for name, value in vars(layout).items()
                if name.isupper() and isinstance(value, str) and value == value.lower()}

    def test_there_are_screens_and_names_to_find(self):
        """The two gates below pass trivially if these ever come back empty."""
        assert len(self._kinds_of_screen()) >= 8
        assert len(self._screen_names()) >= 7

    def test_the_eye_pass_names_no_kind_of_screen(self):
        from fun_time_vr import player

        assert self._kinds_of_screen() & self._names_in(self._source(player._draw_eyes)) == set()

    def test_the_assembly_names_no_kind_of_screen_and_no_screen(self):
        """It is handed what each thing hangs, and walks that."""
        from fun_time_vr import room as under_test

        named = self._names_in(self._source(under_test))

        assert self._kinds_of_screen() & named == set()
        assert self._screen_names() & named == set()

    def test_the_frame_loop_names_no_screen_of_its_own(self):
        """Every screen it works on comes from the room, so adding one is a
        registration rather than a line in here."""
        import ast
        import inspect

        from fun_time_vr import player

        (loop,) = [node for node in ast.walk(ast.parse(inspect.getsource(player._run)))
                   if isinstance(node, ast.While)]

        assert self._screen_names() & self._names_in(ast.unparse(loop)) == set()

    def test_everything_the_room_holds_answers_every_question_it_is_asked(self):
        """Half a screen — pumped but never drawn, drawn but never closed — is
        what threading them by hand kept producing."""
        import ast
        import inspect

        from fun_time_vr import player

        tree = ast.parse(inspect.getsource(player._run))
        built = {node.targets[0].id: node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}

        def made_by(expr):
            if isinstance(expr, ast.Name):
                yield from made_by(built[expr.id])
            elif isinstance(expr, ast.Starred):
                yield from made_by(expr.value)
            elif isinstance(expr, (ast.List, ast.Tuple)):
                for element in expr.elts:
                    yield from made_by(element)
            elif isinstance(expr, ast.ListComp):
                yield from made_by(expr.elt)
            elif isinstance(expr, ast.Call):
                yield getattr(player, ast.unparse(expr.func))

        kinds = list(made_by(built["units"]))

        assert {kind.__name__ for kind in kinds} == self._kinds_the_room_holds()
        for kind in kinds:
            for asked in ("hangings", "hangs_by", "point", "pump",
                          "render_latest_frame", "close"):
                assert callable(getattr(kind, asked, None)), (kind.__name__, asked)

    def test_the_room_is_the_one_list_everything_is_read_off(self):
        """One registration per screen: the same list is what is pumped, what
        the pointer reaches, what is drawn and what a drag moves."""
        import ast
        import inspect

        from fun_time_vr import player

        tree = ast.parse(inspect.getsource(player._run))
        assigned = {ast.unparse(node.targets[0]): ast.unparse(node.value)
                    for node in ast.walk(tree) if isinstance(node, ast.Assign)}
        walked = {ast.unparse(node.iter) for node in ast.walk(tree) if isinstance(node, ast.For)}

        assert "units" in assigned["pumped"]
        assert assigned["hangings"] == "room.what_hangs(units)"
        assert assigned["where"] == "room.where_they_hang(units)"
        assert "units" in walked
