"""The buttons Fun Time declares on the main console, row by row."""
from __future__ import annotations

import pytest
from funestra.console import (
    GAP,
    GROUP_GAP,
    OSR2_CONTROL_BUTTONS,
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
    hit_test,
    place_rows,
    tooltip_at,
)
from funestra.hud_button import BUTTON, Button
from funestra.hud_marks import (
    BROKER_ICON,
    MINIMIZE_ICON,
    SHARED_MARK,
    shared_mark,
    shared_mark_name,
)
from funestra.modes import LengthMode, LoopState, MainMode
from funestra.robot_hand import WaveformShape
from funestra.satellite_hud import HudModel
from funestra.satellite_hud_paint import HudRenderer
from shared_ui.icon_geometry import RENAMED_MARKS, glyph_names

from fun_time import console_buttons
from fun_time.console_buttons import (
    CLIPS_ICON,
    FLAT_ICON,
    FULL_ICON,
    VR_ICON,
    MainSlot,
    console_rows,
    osr2_controls,
    osr2_rows,
)
from fun_time.content import Noun
from fun_time.funestra_buttons import LATEST_ICON
from fun_time.osr2_section import take_osr2_button
from fun_time.room_funestras import RoomFunestra
from tests.symbol_face import typed_in_the_symbol_face

_MINUS, _PLUS = "−", "+"


@pytest.fixture
def pips(monkeypatch):
    """Genau's flicks under a word only this test uses, so a tooltip that names
    them can only have taken the word from the overlay."""
    monkeypatch.setattr(console_buttons, "genau_flick_noun", lambda: Noun(one="pip", many="pips"))


def _every_row(slot: MainSlot) -> tuple[tuple[Button, ...], ...]:
    return (*console_rows(slot), *osr2_rows(slot))


def _actions(slot: MainSlot) -> list[str]:
    return [b.command for row in _every_row(slot) for b in row if b.command]


def _button(slot: MainSlot, action: str) -> Button:
    return next(b for row in _every_row(slot) for b in row if b.command == action)


def _placed(slot: MainSlot) -> dict[str, tuple]:
    return {b.command: rect for rect, b in place_rows(_every_row(slot), x=0, y=0)}


_EVERY_FACE_SLOTS = (
    MainSlot(main_mode=MainMode.KINO, latest=False, length_mode=LengthMode.MIXED, plays_vr=True, plays_flat=True,
             has_compilation=True, has_other_versions=True, jump_to="scene"),
    MainSlot(main_mode=MainMode.KINO, jump_to="clip"),
    MainSlot(main_mode=MainMode.GENAU, latest=False, favorites_filter=False, enhanced_filter=False),
)


def _every_button() -> list[Button]:
    declared = [b for slot in _EVERY_FACE_SLOTS for row in _every_row(slot) for b in row]
    return declared + list(osr2_controls(broker=True))


class TestTheOsr2Switch:
    """One switch saying what the room is doing to the OSR2: on while driving,
    off while parked, and a press flips it."""

    def test_the_switch_is_there_in_both_modes(self):
        for mode in MainMode:
            assert OSR2_CONTROL_BUTTONS[OSR2_PARKED] in _actions(MainSlot(main_mode=mode)), mode

    def test_it_is_on_while_driving_and_a_press_parks(self):
        switch = _button(MainSlot(main_mode=MainMode.KINO, osr2_control=OSR2_DRIVING),
                         OSR2_CONTROL_BUTTONS[OSR2_PARKED])

        assert switch.lit

    @pytest.mark.parametrize("control", [OSR2_PARKED, OSR2_RETRACTED])
    def test_it_is_off_while_held_and_a_press_drives(self, control):
        switch = _button(MainSlot(main_mode=MainMode.KINO, osr2_control=control),
                         OSR2_CONTROL_BUTTONS[OSR2_DRIVING])

        assert not switch.lit


class TestOsr2Controls:
    """The device's own control: the broker that talks to the OSR2 at all."""

    def test_the_broker_is_a_control_and_says_which_way_a_press_goes(self):
        (running,), (down,) = osr2_controls(broker=True), osr2_controls(broker=False)

        assert running.command == down.command == "broker_panel"
        assert running.lit is True
        assert down.lit is False and down.warn is True
        assert (running.tooltip, down.tooltip) == (
            "Broker is running — press to stop it",
            "Broker is not running — press to start it",
        )

    def test_the_broker_asks_for_its_own_icon_rather_than_the_word(self):
        (broker,) = osr2_controls(broker=True)

        assert broker.glyph == BROKER_ICON


class TestTransport:
    """Prev/next step the Main Funestra's video where the Main Funestra is on screen, Genau's flicks where it
    is — with the actions that only make sense for each."""

    def test_kino_mode_steps_the_video_and_acts_on_it(self):
        actions = _actions(MainSlot(main_mode=MainMode.KINO))
        for action in ("main_prev", "main_next", "main_nudge_prev",
                       "main_nudge_next", "main_fmode", "browse_library",
                       "genaumacher_save", "main_player_record_tap"):
            assert action in actions, action

    def test_f_mode_is_the_main_funestras_own_and_lights_while_it_is_on(self):
        assert _button(MainSlot(main_mode=MainMode.KINO), "main_fmode").lit is False
        assert _button(MainSlot(main_mode=MainMode.KINO, scripted_filter=True), "main_fmode").lit is True

    def test_f_mode_is_not_offered_where_there_is_no_main_funestra_playlist(self):
        """In genau mode the main slot is Genau's, and the playlist F-mode
        narrows is not what is playing — the same reason nudge and record go."""
        assert "main_fmode" not in _actions(MainSlot(main_mode=MainMode.GENAU))

    def test_genau_steps_its_own_flicks_and_can_mark_one_weird(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))

        assert "genau_prev_flick" in actions
        assert "genau_next_flick" in actions
        assert "genau_weird_flick" in actions

    def test_genau_can_flip_the_flick_up_half_a_loop(self):
        flip = _button(MainSlot(main_mode=MainMode.GENAU), "genau_flip_ends")

        assert shared_mark_name(flip.glyph) == "flip_ends"
        assert flip.lit is False
        assert _button(MainSlot(main_mode=MainMode.GENAU, flipped=True), "genau_flip_ends").lit is True

    def test_a_video_has_no_flick_to_flip(self):
        assert "genau_flip_ends" not in _actions(MainSlot(main_mode=MainMode.KINO))

    def test_genau_offers_no_video_only_actions(self):
        """Nudge, flick and record act on a video; Genau's flicks are not one."""
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))

        for action in ("main_nudge_prev", "genaumacher_save",
                       "main_player_record_tap", "main_fmode"):
            assert action not in actions

    def test_genau_browses_its_flicks_from_the_same_button_kino_browses_its_library(self, pips):
        kino = _button(MainSlot(main_mode=MainMode.KINO), "browse_library")
        genau = _button(MainSlot(main_mode=MainMode.GENAU), "browse_library")

        assert (kino.glyph, kino.tooltip) == (genau.glyph, "Browse the library")
        assert genau.tooltip == "Browse the pips"

    def test_genaus_transport_names_its_flicks_in_the_overlays_word(self, pips):
        def tips(**slot):
            return {b.command: b.tooltip for b in _every_row(MainSlot(main_mode=MainMode.GENAU, **slot))[1]}

        held, loose = tips(locked=True, pace_s=7), tips(locked=False, pace_s=7)

        assert (held["genau_prev_flick"], held["genau_next_flick"]) == ("Previous pip", "Next pip")
        assert held["main_lock"] == "Locked — this pip repeats; press to move on every 7s"
        assert loose["main_lock"] == "Unlocked — moving on every 7s; press to hold this pip"
        assert tips(flipped=True)["genau_flip_ends"] == "Flipped — this pip stays that way; press to put it back"
        assert tips()["genau_flip_ends"] == ("Flip this pip, for a picture running opposite the OSR2 — "
                                             "it stays flipped")


class TestFavoritesFilter:
    """The other narrowing switch a genau-mode host can have: its favorites."""

    def test_no_button_where_the_host_has_no_such_filter(self):
        assert "main_fmode" not in _actions(MainSlot(main_mode=MainMode.GENAU))

    def test_the_button_appears_once_a_host_says_it_has_one(self):
        assert "main_fmode" in _actions(MainSlot(main_mode=MainMode.GENAU, favorites_filter=False))

    def test_it_lights_while_the_filter_is_on(self):
        off = _button(MainSlot(main_mode=MainMode.GENAU, favorites_filter=False), "main_fmode")
        on = _button(MainSlot(main_mode=MainMode.GENAU, favorites_filter=True), "main_fmode")

        assert (off.lit, on.lit) == (False, True)
        assert on.favorite is True   # green: the favorites own it across the family

    def test_it_leads_the_switches_where_it_leads_them_in_the_other_branch(self):
        """F holds one place on this console whichever branch drew it, and the
        rest of the narrowing switches group after it."""
        actions = _actions(MainSlot(main_mode=MainMode.GENAU, favorites_filter=False, enhanced_filter=False))

        assert actions.index("main_fmode") == actions.index("main_lock") + 1
        assert actions.index("genau_filter_enhanced") == actions.index("main_fmode") + 1


class TestEnhancedFilter:
    """Origenerator's own narrowing switch: keep only the pictures it enhanced."""

    def test_no_button_where_the_host_has_no_such_filter(self):
        assert "genau_filter_enhanced" not in _actions(MainSlot(main_mode=MainMode.GENAU))
        assert "genau_filter_enhanced" not in _actions(MainSlot(main_mode=MainMode.KINO))

    def test_it_lights_and_says_which_way_the_press_goes(self):
        off = _button(MainSlot(main_mode=MainMode.GENAU, enhanced_filter=False), "genau_filter_enhanced")
        on = _button(MainSlot(main_mode=MainMode.GENAU, enhanced_filter=True), "genau_filter_enhanced")

        assert (off.lit, on.lit) == (False, True)
        assert "Show only" in off.tooltip
        assert "press for all of them" in on.tooltip

    def test_it_keeps_the_yellow_an_enhancement_is_marked_with(self):
        button = _button(MainSlot(main_mode=MainMode.GENAU, enhanced_filter=True), "genau_filter_enhanced")

        assert button.enhanced is True
        assert button.favorite is False

    def test_it_sits_with_the_switches_after_the_lock(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU, enhanced_filter=False))

        assert actions.index("genau_filter_enhanced") == actions.index("main_lock") + 1


class TestReset:
    """The way back out: drop everything narrowing what the Main Funestra plays."""

    def test_the_main_funestra_can_be_reset_wherever_main_funestra_is_on_screen(self):
        assert "main_reset" in _actions(MainSlot(main_mode=MainMode.KINO))

    def test_it_is_not_offered_where_there_is_no_main_funestra_playlist(self):
        assert "main_reset" not in _actions(MainSlot(main_mode=MainMode.GENAU))

    def test_it_is_a_thing_done_rather_than_a_state_held(self):
        for scripted_filter in (False, True):
            button = _button(MainSlot(main_mode=MainMode.KINO, scripted_filter=scripted_filter),
                             "main_reset")
            assert button.lit is False
            assert button.favorite is False

    def test_it_stands_clear_of_the_switches_it_turns_off(self):
        by_action = _placed(MainSlot(main_mode=MainMode.KINO))
        fmode, reset = by_action["main_fmode"], by_action["main_reset"]

        assert reset[0] - (fmode[0] + fmode[2]) == GROUP_GAP


class TestBrowseOrder:
    """Shuffle and Latest: which way round what plays was put in order."""

    def test_both_funestras_can_be_reordered_from_the_console(self):
        for mode in MainMode:
            actions = _actions(MainSlot(main_mode=mode, latest=False))
            assert "main_shuffle" in actions and "main_latest" in actions

    def test_exactly_one_of_the_pair_is_lit(self):
        shuffled, newest = MainSlot(main_mode=MainMode.KINO, latest=False), MainSlot(main_mode=MainMode.KINO, latest=True)

        assert _button(shuffled, "main_shuffle").lit is True
        assert _button(shuffled, "main_latest").lit is False
        assert _button(newest, "main_shuffle").lit is False
        assert _button(newest, "main_latest").lit is True

    def test_neither_of_the_pair_wears_a_color_of_its_own(self):
        for action in ("main_shuffle", "main_latest"):
            button = _button(MainSlot(main_mode=MainMode.KINO, latest=False), action)
            assert button.favorite is False and button.enhanced is False

    def test_the_pair_is_its_own_group_after_the_reset(self):
        by_action = _placed(MainSlot(main_mode=MainMode.KINO, latest=False))
        reset = by_action["main_reset"]
        shuffle, latest = by_action["main_shuffle"], by_action["main_latest"]

        assert shuffle[0] - (reset[0] + reset[2]) == GROUP_GAP
        assert latest[0] - (shuffle[0] + shuffle[2]) == GAP

    def test_a_host_with_no_browse_order_is_offered_neither(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))

        assert "main_shuffle" not in actions and "main_latest" not in actions


class TestProjectionPair:
    """Flat and VR, as the two shapes of video each button includes."""

    def _flat_and_vr(self, *, flat, vr, **over) -> tuple[Button, ...]:
        rows = console_rows(MainSlot(main_mode=MainMode.KINO, latest=False, length_mode=LengthMode.MIXED,
                                     plays_flat=flat, plays_vr=vr, **over))
        by_face = {b.glyph: b for row in rows for b in row if b.command.startswith("main_projection")}
        return tuple(by_face[face] for face in (FLAT_ICON, VR_ICON) if face in by_face)

    def test_both_shapes_is_both_of_them_lit(self):
        assert [b.lit for b in self._flat_and_vr(flat=True, vr=True)] == [True, True]

    def test_dropping_one_from_both_asks_for_the_other_alone(self):
        flat, vr = self._flat_and_vr(flat=True, vr=True)

        assert (flat.command, vr.command) == ("main_projection_vr", "main_projection_flat")

    def test_putting_the_dark_one_back_asks_for_both(self):
        flat, vr = self._flat_and_vr(flat=False, vr=True)

        assert (flat.lit, vr.lit) == (False, True)
        assert flat.command == "main_projection_both"

    def test_the_last_lit_one_can_still_be_turned_off(self):
        flat, vr = self._flat_and_vr(flat=True, vr=False)

        assert (flat.dim, vr.dim) == (False, False)
        assert flat.command == "main_projection_none"

    def test_neither_lit_offers_each_shape_back(self):
        flat, vr = self._flat_and_vr(flat=False, vr=False)

        assert (flat.command, vr.command) == ("main_projection_flat", "main_projection_vr")

    def test_flat_leads_vr_in_a_group_of_their_own(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.KINO, latest=False,
                                                  plays_flat=True, plays_vr=True)), x=0, y=0)
        rect_of = {b.glyph: rect for rect, b in placed}
        latest, flat, vr = rect_of[LATEST_ICON], rect_of[FLAT_ICON], rect_of[VR_ICON]

        assert flat[0] - (latest[0] + latest[2]) == GROUP_GAP
        assert vr[0] - (flat[0] + flat[2]) == GAP

    def test_no_pair_at_all_where_the_library_holds_one_shape(self):
        assert self._flat_and_vr(flat=None, vr=None) == ()

    def test_inside_a_compilation_the_shapes_read_as_held(self):
        pair = self._flat_and_vr(flat=True, vr=True, compilation="Volume 6")

        assert pair and all(b.remembered and not b.lit for b in pair)


class TestGenausProjectionPair:
    """The same pair over Genau's flicks, where its folders hold both shapes."""

    def _pair(self, plays_vr, plays_flat):
        rows = console_rows(MainSlot(main_mode=MainMode.GENAU, latest=False,
                                     plays_vr=plays_vr, plays_flat=plays_flat))
        return [b for row in rows for b in row if b.command.startswith("main_projection")]

    def _faces_after_latest(self, main_mode: MainMode) -> list[str]:
        faces = [b.glyph for row in console_rows(MainSlot(main_mode=main_mode, latest=False,
                                                           plays_vr=True, plays_flat=True))
                 for b in row]
        return faces[faces.index(LATEST_ICON) + 1:][:2]

    def test_it_follows_the_browse_order_as_it_does_under_a_video(self):
        assert self._faces_after_latest(MainMode.GENAU) == self._faces_after_latest(MainMode.KINO) != []

    def test_it_names_the_flicks_where_the_video_pair_says_videos(self, pips):
        tips = [{b.tooltip for b in self._pair(*plays)}
                for plays in ((True, True), (True, False), (False, False))]

        assert tips == [
            {"Including VR pips", "Including 2D pips"},
            {"Including VR pips", "Not including 2D pips"},
            {"Not including VR pips", "Not including 2D pips"},
        ]


class TestLengthPair:
    """Clips and full, as the two lengths each button includes."""

    def _clips_and_full(self, length_mode: LengthMode, **over) -> tuple[Button, ...]:
        rows = console_rows(MainSlot(main_mode=MainMode.KINO, latest=False, length_mode=length_mode, **over))
        by_face = {b.glyph: b for row in rows for b in row if b.command.startswith("main_player_length")}
        return tuple(by_face[face] for face in (CLIPS_ICON, FULL_ICON) if face in by_face)

    def test_mixed_is_both_of_them_lit(self):
        assert [b.lit for b in self._clips_and_full(LengthMode.MIXED)] == [True, True]

    def test_one_length_is_that_one_lit_and_the_other_dark(self):
        assert [b.lit for b in self._clips_and_full(LengthMode.CLIPS)] == [True, False]
        assert [b.lit for b in self._clips_and_full(LengthMode.FULL)] == [False, True]

    def test_dropping_one_from_mixed_asks_for_the_other_alone(self):
        clips, full = self._clips_and_full(LengthMode.MIXED)

        assert (clips.command, full.command) == ("main_player_length_full", "main_player_length_clips")

    def test_putting_the_dark_one_back_asks_for_mixed(self):
        clips, _full = self._clips_and_full(LengthMode.FULL)

        assert clips.command == "main_player_length_mixed"

    def test_the_last_lit_one_can_still_be_turned_off(self):
        clips, full = self._clips_and_full(LengthMode.FULL)

        assert (clips.dim, full.dim) == (False, False)
        assert full.command == "main_player_length_none"

    def test_neither_lit_offers_each_length_back(self):
        clips, full = self._clips_and_full(LengthMode.NONE)

        assert (clips.lit, full.lit) == (False, False)
        assert (clips.command, full.command) == ("main_player_length_clips", "main_player_length_full")

    def test_clips_lead_full_in_a_group_of_their_own(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.KINO, latest=False,
                                                  length_mode=LengthMode.MIXED)), x=0, y=0)
        rect_of = {b.glyph: rect for rect, b in placed}
        latest, clips, full = rect_of[LATEST_ICON], rect_of[CLIPS_ICON], rect_of[FULL_ICON]

        assert clips[0] - (latest[0] + latest[2]) == GROUP_GAP
        assert full[0] - (clips[0] + clips[2]) == GAP

    def test_each_says_whether_its_length_is_included(self):
        clips, full = self._clips_and_full(LengthMode.CLIPS)

        assert (clips.tooltip, full.tooltip) == ("Including clips", "Not including full videos")


class TestCompilationAndJumps:
    """The set a video belongs to, the scene it came from, and its other cuts."""

    def _button_for(self, action: str, **over):
        return _button(MainSlot(main_mode=MainMode.KINO, latest=False, length_mode=LengthMode.MIXED, **over), action)

    def test_the_compilation_button_enters_and_the_lit_one_leaves(self):
        outside = self._button_for("main_player_compilation", has_compilation=True)
        inside = self._button_for("main_player_end_compilation", compilation="Volume 6")

        assert outside.lit is False and outside.dim is False
        assert inside.lit is True

    def test_a_video_in_no_compilation_cannot_be_pressed_into_one(self):
        assert self._button_for("main_player_compilation").dim is True

    def test_inside_a_compilation_the_order_and_length_read_as_held(self):
        rows = console_rows(MainSlot(main_mode=MainMode.KINO, latest=False, length_mode=LengthMode.MIXED,
                                     compilation="Volume 6"))
        held = [b for row in rows for b in row
                if b.command in ("main_shuffle", "main_player_length_clips")]

        assert held and all(b.remembered and not b.lit for b in held)

    def test_the_clip_jump_says_which_way_it_would_go(self):
        to_scene = self._button_for("main_player_full_vid", jump_to="scene")
        to_clip = self._button_for("main_player_clip_jump", jump_to="clip")

        assert to_scene.glyph != to_clip.glyph
        assert to_scene.dim is False and to_clip.dim is False

    def test_a_video_with_nothing_on_the_other_end_is_dim(self):
        assert self._button_for("main_player_clip_jump").dim is True

    def test_the_version_step_is_dim_without_another_version(self):
        assert self._button_for("main_player_cycle_version").dim is True
        assert self._button_for("main_player_cycle_version", has_other_versions=True).dim is False


class TestLock:
    """The padlock: whether the video repeats or plays on into the playlist."""

    def test_it_is_lit_while_the_video_is_held(self):
        assert _button(MainSlot(main_mode=MainMode.KINO, locked=True), "main_lock").lit is True
        assert _button(MainSlot(main_mode=MainMode.KINO, locked=False), "main_lock").lit is False

    def test_it_says_which_way_a_press_goes(self):
        held = _button(MainSlot(main_mode=MainMode.KINO, locked=True), "main_lock")
        loose = _button(MainSlot(main_mode=MainMode.KINO, locked=False), "main_lock")

        assert held.tooltip.startswith("Locked") and "play on" in held.tooltip
        assert loose.tooltip.startswith("Unlocked") and "hold this video" in loose.tooltip

    def test_it_pairs_with_f_mode_and_stands_apart_from_everything_else(self):
        by_action = _placed(MainSlot(main_mode=MainMode.KINO))
        step, lock = by_action["main_next"], by_action["main_lock"]
        fmode, reset = by_action["main_fmode"], by_action["main_reset"]

        assert lock[0] - (step[0] + step[2]) == GROUP_GAP
        assert fmode[0] - (lock[0] + lock[2]) == GAP
        assert reset[0] - (fmode[0] + fmode[2]) == GROUP_GAP

    def test_every_mode_offers_exactly_one_padlock_lit_while_whatever_shows_is_held(self):
        for mode in MainMode:
            assert _actions(MainSlot(main_mode=mode)).count("main_lock") == 1, mode
            assert _button(MainSlot(main_mode=mode, locked=True), "main_lock").lit is True
            assert _button(MainSlot(main_mode=mode, locked=False), "main_lock").lit is False

    def test_in_genau_it_says_what_the_clip_does_and_how_fast(self):
        held = _button(MainSlot(main_mode=MainMode.GENAU, locked=True, pace_s=7), "main_lock")
        loose = _button(MainSlot(main_mode=MainMode.GENAU, locked=False, pace_s=7), "main_lock")

        assert held.tooltip.startswith("Locked") and "every 7s" in held.tooltip
        assert loose.tooltip.startswith("Unlocked") and "every 7s" in loose.tooltip


class TestPaceRows:
    """The video's playback rate under a video, Genau's flick pace in genau mode:
    a named row, a read-out the drawing host fills, and the pair around it."""

    def test_the_video_rate_has_controls_where_main_funestra_is_on_screen(self):
        actions = _actions(MainSlot(main_mode=MainMode.KINO))
        assert "main_player_speed_down" in actions and "main_player_speed_up" in actions
        assert "genau_flick_seconds_down" not in actions

    def test_genau_has_the_pace_and_no_video_rate(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))
        assert "genau_flick_seconds_down" in actions and "genau_flick_seconds_up" in actions
        assert "main_player_speed_down" not in actions

    def test_the_rate_is_a_read_out_between_the_arrows_that_the_funestra_fills(self):
        row = next(row for row in console_rows(MainSlot(main_mode=MainMode.KINO))
                   if any(b.command == "main_player_speed_down" for b in row))

        assert [b.glyph or b.host_value for b in row] == [
            "Playback speed", _MINUS, "playback_speed", _PLUS]

    def test_the_seconds_are_a_read_out_between_the_arrows_that_genau_fills(self, pips):
        row = next(row for row in console_rows(MainSlot(main_mode=MainMode.GENAU))
                   if any(b.command == "genau_flick_seconds_down" for b in row))

        assert [b.glyph or b.host_value for b in row] == [
            "Pip seconds", _MINUS, "advance_interval", _PLUS]
        assert row[-1].tooltip == "Leave each pip longer"

    def test_a_read_out_is_not_a_hit_target(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.KINO)), x=0, y=0)
        rect = next(r for r, b in placed if b.host_value == "playback_speed")

        assert hit_test(placed, rect[0] + 1, rect[1] + 1) == ""

    def test_the_rate_pair_says_what_a_satellites_rate_pair_says(self):
        """The satellites draw their own pair, in the Funestras' words; one
        control reads the same on every Funestra."""
        rendered = HudRenderer("landscape").render(
            HudModel(funestra="landscape", playback_speed=1.0))
        satellite = [button.tooltip for _rect, button in rendered.targets.buttons
                     if button.command.startswith("landscape_speed_")]
        console = [button.tooltip
                   for row in console_rows(MainSlot(main_mode=MainMode.KINO))
                   for button in row if button.command.startswith("main_player_speed_")]

        assert satellite == console != []


class TestDriveControls:
    def test_the_switch_row_is_there_in_both_modes(self):
        for mode in MainMode:
            actions = _actions(MainSlot(main_mode=mode))
            for action in ("robot_hand_toggle_learned", "robot_hand_cycle_shape", "quarter_button",
                           "robot_hand_park"):
                assert action in actions, (mode, action)

    def test_the_funscript_jump_rides_the_row_under_a_video_alone(self):
        assert "main_player_funscript_jump" in _actions(MainSlot(main_mode=MainMode.KINO))
        assert "main_player_funscript_jump" not in _actions(MainSlot(main_mode=MainMode.GENAU))

    def test_the_axis_arrows_are_not_console_buttons(self):
        actions = _actions(MainSlot(main_mode=MainMode.KINO))

        for action in ("robot_hand_amplitude_up", "robot_hand_center_down", "robot_hand_speed_up"):
            assert action not in actions

    def test_human_inspired_motion_follows_the_switch_and_wears_the_stick_figure(self):
        for mode in MainMode:
            row = osr2_rows(MainSlot(main_mode=mode))[0]
            commands = [b.command for b in row if b.command]
            assert commands[1:4] == [
                "robot_hand_toggle_learned", "robot_hand_cycle_shape", "quarter_button"], mode
        assert (_button(MainSlot(main_mode=MainMode.GENAU), "robot_hand_toggle_learned").glyph
                == shared_mark("human"))
        assert _button(MainSlot(main_mode=MainMode.GENAU), "robot_hand_toggle_learned").lit is False
        lit = _button(MainSlot(main_mode=MainMode.GENAU, learned=True), "robot_hand_toggle_learned")
        assert lit.lit is True
        assert (lit.favorite, lit.warn, lit.hold) == (False, False, False)

    def test_the_waveform_button_names_the_waveform(self):
        slot = MainSlot(main_mode=MainMode.GENAU, shape="rounded_square")

        assert _button(slot, "robot_hand_cycle_shape").tooltip == "Waveform: Square"

    def test_the_waveform_button_wears_the_waveform_the_motion_is_in(self):
        faces = {_button(MainSlot(main_mode=MainMode.GENAU, shape=shape.value),
                         "robot_hand_cycle_shape").glyph
                 for shape in WaveformShape}

        assert len(faces) == len(WaveformShape)


class TestTheRowsThatAimTheDevice:
    def test_ride_with_the_device_rather_than_among_the_rows_above_its_line(self):
        slot = MainSlot(main_mode=MainMode.VIDEO)

        assert "robot_hand_park" in [b.command for row in osr2_rows(slot) for b in row]
        assert "robot_hand_park" not in [b.command for row in console_rows(slot) for b in row]

    def test_a_console_that_gave_the_osr2_away_offers_it_back_there(self):
        slot = MainSlot(main_mode=MainMode.VIDEO, has_osr2=False)

        assert osr2_rows(slot) == ((take_osr2_button(RoomFunestra.MAIN),),)
        assert take_osr2_button(RoomFunestra.MAIN) not in [b for row in console_rows(slot) for b in row]


class TestState:
    def test_the_mode_you_are_in_is_lit_and_the_others_are_not(self):
        slot = MainSlot(main_mode=MainMode.KINO)

        assert _button(slot, "main_kino_activate").lit is True
        assert _button(slot, "genau_activate").lit is False

    def test_nothing_but_the_recording_and_its_loop_takes_a_color_of_its_own(self):
        colored = [b.command for row in _every_row(MainSlot(main_mode=MainMode.GENAU, learned=True, locked=True))
                   for b in row if b.warn or b.hold]

        assert colored == []

    def test_f_mode_keeps_the_green_the_other_switches_gave_up(self):
        assert _button(MainSlot(main_mode=MainMode.KINO, scripted_filter=True), "main_fmode").favorite is True
        assert _button(MainSlot(main_mode=MainMode.GENAU), "robot_hand_toggle_learned").favorite is False

    def test_the_record_button_tells_marking_from_looping(self):
        idle = _button(MainSlot(main_mode=MainMode.KINO), "main_player_record_tap")
        marking = _button(MainSlot(main_mode=MainMode.KINO, loop_state=LoopState.RECORDING), "main_player_record_tap")
        looping = _button(MainSlot(main_mode=MainMode.KINO, loop_state=LoopState.LOOPING), "main_player_record_tap")

        assert (idle.warn, idle.hold) == (False, False)
        assert (marking.warn, marking.hold) == (True, False)
        assert (looping.warn, looping.hold) == (False, True)

    def test_the_record_button_says_which_press_comes_next(self):
        for loop_state, wanted in ((LoopState.NORMAL, "Record"), (LoopState.RECORDING, "out point"),
                                   (LoopState.LOOPING, "drop the loop")):
            assert wanted in _button(MainSlot(main_mode=MainMode.KINO, loop_state=loop_state),
                                     "main_player_record_tap").tooltip


class TestLayout:
    def test_the_mode_pair_names_kino_and_genau(self):
        first = console_rows(MainSlot())[0]

        assert [(b.command, b.glyph, b.tooltip) for b in first[:2]] == [
            ("main_kino_activate", "Kino", "Kino mode"), ("genau_activate", "Genau", "Genau mode")]

    def test_the_mode_row_leads_so_it_holds_its_place_across_modes(self):
        for mode in MainMode:
            first = console_rows(MainSlot(main_mode=mode))[0]
            assert [b.command for b in first][:4] == [
                "main_kino_activate", "genau_activate", "main_minimize", "main_crown"]

    def test_the_browse_rides_the_mode_row_in_both_modes_and_the_rest_only_over_a_video(self):
        video = [b.command for b in console_rows(MainSlot(main_mode=MainMode.KINO))[0]]
        genau = [b.command for b in console_rows(MainSlot(main_mode=MainMode.GENAU))[0]]

        assert video[4:] == ["browse_library", "main_player_record_tap", "genaumacher_save"]
        assert genau[4:] == ["browse_library"]

    def test_the_file_actions_stand_apart_from_the_crown_and_from_each_other(self):
        by_action = _placed(MainSlot(main_mode=MainMode.KINO))
        crown, browse = by_action["main_crown"], by_action["browse_library"]
        record, save = by_action["main_player_record_tap"], by_action["genaumacher_save"]

        assert browse[0] - (crown[0] + crown[2]) == GROUP_GAP
        assert record[0] - (browse[0] + browse[2]) == GROUP_GAP
        assert save[0] - (record[0] + record[2]) == GAP

    def test_the_crown_is_lit_while_the_main_funestra_holds_it(self):
        held = _button(MainSlot(crowned=True), "main_crown")
        given_away = _button(MainSlot(crowned=False), "main_crown")

        assert (held.lit, given_away.lit) == (True, False)
        assert held.tooltip.startswith("Crowned")

    def test_the_crown_sits_beside_minimize(self):
        by_action = _placed(MainSlot(main_mode=MainMode.KINO))
        minimize, crown = by_action["main_minimize"], by_action["main_crown"]

        assert crown[0] - (minimize[0] + minimize[2]) == GAP

    def test_minimize_rides_the_row_that_never_changes(self):
        video, genau = _placed(MainSlot(main_mode=MainMode.KINO)), _placed(MainSlot(main_mode=MainMode.GENAU))

        assert video["main_minimize"] == genau["main_minimize"]

    def test_minimize_stands_apart_from_the_modes_it_sits_beside(self):
        by_action = _placed(MainSlot(main_mode=MainMode.KINO))
        genau, minimize = by_action["genau_activate"], by_action["main_minimize"]

        assert minimize[0] - (genau[0] + genau[2]) == GROUP_GAP

    def test_a_console_in_the_headset_has_no_window_to_minimize(self):
        """The Funestras under it are screens in the scene there, not windows, so
        the mode pair runs straight on into the file controls."""
        video = [b.command for b in console_rows(MainSlot(main_mode=MainMode.KINO), in_vr=True)[0]]
        genau = [b.command for b in console_rows(MainSlot(main_mode=MainMode.GENAU), in_vr=True)[0]]

        assert video == ["main_kino_activate", "genau_activate",
                         "browse_library", "main_player_record_tap", "genaumacher_save"]
        assert genau == ["main_kino_activate", "genau_activate", "browse_library"]

    def test_minimize_asks_for_a_drawn_bar_rather_than_a_font_glyph(self):
        button = _button(MainSlot(main_mode=MainMode.GENAU), "main_minimize")

        assert button.glyph == MINIMIZE_ICON
        assert "taskbar" in button.tooltip

    def test_a_press_finds_the_button_under_it_and_names_it(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.KINO)), x=0, y=0)
        rect = next(r for r, b in placed if b.command == "main_next")

        assert hit_test(placed, rect[0] + 1, rect[1] + 1) == "main_next"
        assert tooltip_at(placed, rect[0] + 1, rect[1] + 1) == "Next video"

    def test_the_buttons_are_the_familys_size(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.KINO)), x=0, y=0)

        assert all(rect[3] == BUTTON for rect, _b in placed)


class TestFaces:
    def test_every_typed_face_is_in_the_painters_symbol_face(self):
        typed = {b.glyph for b in _every_button() if len(b.glyph) == 1 and not b.glyph.isalnum()}

        assert typed
        for face in typed:
            assert typed_in_the_symbol_face(face), ascii(face)

    def test_every_mark_a_button_names_is_one_the_family_draws(self):
        named = {shared_mark_name(b.glyph) for b in _every_button()
                 if b.glyph.startswith(SHARED_MARK)}

        assert {"trash", "reset", "wave"} <= named
        assert not named - set(glyph_names())

    def test_no_button_asks_for_a_mark_by_a_name_the_family_has_retired(self):
        named = {shared_mark_name(b.glyph) for b in _every_button()
                 if b.glyph.startswith(SHARED_MARK)}

        assert not named & set(RENAMED_MARKS)

    def test_the_bin_takes_something_away(self):
        assert _button(MainSlot(main_mode=MainMode.GENAU), "genau_weird_flick").danger is True
