"""The buttons Fun Time declares on the main console, row by row."""
from __future__ import annotations

import itertools

from player_core.console import (
    GAP,
    GROUP_GAP,
    OSR2_CONTROL_BUTTONS,
    OSR2_CONTROL_OFF,
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
    hit_test,
    place_rows,
    tooltip_at,
)
from player_core.hud_button import BUTTON, Button
from player_core.hud_marks import BROKER_ICON, MINIMIZE_ICON, SHARED_MARK, shared_mark_name
from player_core.modes import LengthMode, LoopState, MainMode
from shared_ui.icon_geometry import glyph_names

from fun_time.console_buttons import MainSlot, console_rows, osr2_controls, shape_label
from tests.symbol_face import typed_in_the_symbol_face

_MINUS, _PLUS = "−", "+"


def _actions(slot: MainSlot) -> list[str]:
    return [b.command for row in console_rows(slot) for b in row if b.command]


def _button(slot: MainSlot, action: str) -> Button:
    return next(b for row in console_rows(slot) for b in row if b.command == action)


def _placed(slot: MainSlot) -> dict[str, tuple]:
    return {b.command: rect for rect, b in place_rows(console_rows(slot), x=0, y=0)}


_EVERY_FACE_SLOTS = (
    MainSlot(main_mode=MainMode.VIDEO, latest=False, length_mode=LengthMode.MIXED, plays_vr=True, plays_flat=True,
             has_compilation=True, has_other_versions=True, jump_to="scene"),
    MainSlot(main_mode=MainMode.VIDEO, jump_to="clip"),
    MainSlot(main_mode=MainMode.GENAU, latest=False, favorites_filter=False, enhanced_filter=False),
)


def _every_button() -> list[Button]:
    declared = [b for slot in _EVERY_FACE_SLOTS for row in console_rows(slot) for b in row]
    return declared + list(osr2_controls(broker=True))


class TestOsr2ControlStates:
    """parked / retracted / driving / control off: one radio group saying what
    the room is doing to the OSR2, with exactly one of the four lit."""

    def test_every_state_has_a_button_in_both_modes(self):
        for mode in MainMode:
            actions = _actions(MainSlot(main_mode=mode))
            for action in OSR2_CONTROL_BUTTONS.values():
                assert action in actions, (mode, action)

    def test_the_state_it_is_in_is_the_one_that_lights(self):
        for state in OSR2_CONTROL_BUTTONS:
            slot = MainSlot(main_mode=MainMode.VIDEO, osr2_control=state)
            for other, action in OSR2_CONTROL_BUTTONS.items():
                drawn = _button(slot, action)
                assert (drawn.lit or drawn.warn) is (other == state), (state, other)

    def test_control_off_lights_red_where_the_other_three_light_blue(self):
        """Blue is this family's "engaged"; red is the one press that means the
        device is hearing nothing, and what the pill below then reads in."""
        for state in (OSR2_PARKED, OSR2_RETRACTED, OSR2_DRIVING):
            drawn = _button(MainSlot(main_mode=MainMode.VIDEO, osr2_control=state),
                            OSR2_CONTROL_BUTTONS[state])
            assert (drawn.lit, drawn.warn) == (True, False), state

        off = _button(MainSlot(main_mode=MainMode.VIDEO, osr2_control=OSR2_CONTROL_OFF),
                      OSR2_CONTROL_BUTTONS[OSR2_CONTROL_OFF])
        assert (off.lit, off.warn) == (False, True)

    def test_they_read_from_off_to_on_left_to_right(self):
        """One control in four states, in one group with no break inside it, and
        laid out as the switch it is: off at the left, then the two holds, then
        the device driving at the right."""
        placed = _placed(MainSlot(main_mode=MainMode.GENAU))
        edges = [placed[OSR2_CONTROL_BUTTONS[state]]
                 for state in (OSR2_CONTROL_OFF, OSR2_PARKED, OSR2_RETRACTED,
                               OSR2_DRIVING)]
        gaps = [nxt[0] - (rect[0] + rect[2])
                for rect, nxt in itertools.pairwise(edges)]

        assert gaps == [GAP, GAP, GAP]


class TestShapeLabel:
    """The control that cycles the waveform names it on hover."""

    def test_names_the_waveform_instead_of_leaving_it_to_the_curve(self):
        assert shape_label("sine") == "Sine"
        assert shape_label("rounded_square") == "Square"
        assert shape_label("sawtooth") == "Sawtooth"

    def test_an_unknown_shape_is_titled_rather_than_dropped(self):
        assert shape_label("half_moon") == "Half Moon"


class TestOsr2Controls:
    """The device's own control: the broker that talks to the OSR2 at all."""

    def test_the_broker_is_a_control_and_says_which_way_a_press_goes(self):
        (running,), (down,) = osr2_controls(broker=True), osr2_controls(broker=False)

        assert running.command == down.command == "broker_panel"
        assert running.lit is True and "stop" in running.tooltip
        assert down.lit is False and down.warn is True and "start" in down.tooltip

    def test_the_broker_asks_for_its_own_icon_rather_than_the_word(self):
        (broker,) = osr2_controls(broker=True)

        assert broker.glyph == BROKER_ICON


class TestTransport:
    """Prev/next step the main player's video where the main player is on screen, Genau's clips where it
    is — with the actions that only make sense for each."""

    def test_video_mode_steps_the_video_and_acts_on_it(self):
        actions = _actions(MainSlot(main_mode=MainMode.VIDEO))
        for action in ("main_prev", "main_next", "main_nudge_prev",
                       "main_nudge_next", "main_fmode", "browse_library",
                       "clipper_save", "main_player_record_tap"):
            assert action in actions, action

    def test_f_mode_is_the_main_players_own_and_lights_while_it_is_on(self):
        assert _button(MainSlot(main_mode=MainMode.VIDEO), "main_fmode").lit is False
        assert _button(MainSlot(main_mode=MainMode.VIDEO, scripted_filter=True), "main_fmode").lit is True

    def test_f_mode_is_not_offered_where_there_is_no_main_player_playlist(self):
        """In genau mode the main slot is Genau's, and the playlist F-mode
        narrows is not what is playing — the same reason nudge and record go."""
        assert "main_fmode" not in _actions(MainSlot(main_mode=MainMode.GENAU))

    def test_genau_steps_its_own_clips_and_can_mark_one_weird(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))

        assert "genau_prev_clip" in actions
        assert "genau_next_clip" in actions
        assert "genau_weird_clip" in actions

    def test_genau_offers_no_video_only_actions(self):
        """Nudge, open, clip and record act on a video; Genau's clips are not one."""
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))

        for action in ("main_nudge_prev", "browse_library", "clipper_save",
                       "main_player_record_tap", "main_fmode"):
            assert action not in actions


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
        assert "genau_filter_enhanced" not in _actions(MainSlot(main_mode=MainMode.VIDEO))

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
    """The way back out: drop everything narrowing what the main player plays."""

    def test_the_main_player_can_be_reset_wherever_main_player_is_on_screen(self):
        assert "main_reset" in _actions(MainSlot(main_mode=MainMode.VIDEO))

    def test_it_is_not_offered_where_there_is_no_main_player_playlist(self):
        assert "main_reset" not in _actions(MainSlot(main_mode=MainMode.GENAU))

    def test_it_is_a_thing_done_rather_than_a_state_held(self):
        for scripted_filter in (False, True):
            button = _button(MainSlot(main_mode=MainMode.VIDEO, scripted_filter=scripted_filter),
                             "main_reset")
            assert button.lit is False
            assert button.favorite is False

    def test_it_stands_clear_of_the_switches_it_turns_off(self):
        by_action = _placed(MainSlot(main_mode=MainMode.VIDEO))
        fmode, reset = by_action["main_fmode"], by_action["main_reset"]

        assert reset[0] - (fmode[0] + fmode[2]) == GROUP_GAP


class TestBrowseOrder:
    """Shuffle and Latest: which way round what plays was put in order."""

    def test_both_players_can_be_reordered_from_the_console(self):
        for mode in MainMode:
            actions = _actions(MainSlot(main_mode=mode, latest=False))
            assert "main_shuffle" in actions and "main_latest" in actions

    def test_exactly_one_of_the_pair_is_lit(self):
        shuffled, newest = MainSlot(main_mode=MainMode.VIDEO, latest=False), MainSlot(main_mode=MainMode.VIDEO, latest=True)

        assert _button(shuffled, "main_shuffle").lit is True
        assert _button(shuffled, "main_latest").lit is False
        assert _button(newest, "main_shuffle").lit is False
        assert _button(newest, "main_latest").lit is True

    def test_neither_of_the_pair_wears_a_color_of_its_own(self):
        for action in ("main_shuffle", "main_latest"):
            button = _button(MainSlot(main_mode=MainMode.VIDEO, latest=False), action)
            assert button.favorite is False and button.enhanced is False

    def test_the_pair_is_its_own_group_after_the_reset(self):
        by_action = _placed(MainSlot(main_mode=MainMode.VIDEO, latest=False))
        reset = by_action["main_reset"]
        shuffle, latest = by_action["main_shuffle"], by_action["main_latest"]

        assert shuffle[0] - (reset[0] + reset[2]) == GROUP_GAP
        assert latest[0] - (shuffle[0] + shuffle[2]) == GAP

    def test_a_host_with_no_browse_order_is_offered_neither(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))

        assert "main_shuffle" not in actions and "main_latest" not in actions


class TestProjectionPair:
    """VR and flat, as the two shapes of video each button includes."""

    def _pair(self, plays_vr, plays_flat, **over):
        rows = console_rows(MainSlot(main_mode=MainMode.VIDEO, latest=False, length_mode=LengthMode.MIXED,
                                     plays_vr=plays_vr, plays_flat=plays_flat, **over))
        return [b for row in rows for b in row if b.command.startswith("main_projection")]

    def test_both_shapes_is_both_of_them_lit(self):
        assert [b.lit for b in self._pair(True, True)] == [True, True]

    def test_dropping_one_from_both_asks_for_the_other_alone(self):
        assert [b.command for b in self._pair(True, True)] == [
            "main_projection_flat", "main_projection_vr"]

    def test_putting_the_dark_one_back_asks_for_both(self):
        pair = self._pair(True, False)

        assert [b.lit for b in pair] == [True, False]
        assert pair[1].command == "main_projection_both"

    def test_the_last_lit_one_can_still_be_turned_off(self):
        pair = self._pair(False, True)

        assert [b.dim for b in pair] == [False, False]
        assert pair[1].command == "main_projection_none"

    def test_neither_lit_offers_each_shape_back(self):
        assert [b.command for b in self._pair(False, False)] == [
            "main_projection_vr", "main_projection_flat"]

    def test_no_pair_at_all_where_the_library_holds_one_shape(self):
        assert self._pair(None, None) == []

    def test_inside_a_compilation_the_shapes_read_as_held(self):
        pair = self._pair(True, True, compilation="Volume 6")

        assert all(b.remembered and not b.lit for b in pair)


class TestLengthPair:
    """Full length and shorts, as the two lengths each button includes."""

    def _pair(self, length_mode: LengthMode | None, **over):
        rows = console_rows(MainSlot(main_mode=MainMode.VIDEO, latest=False, length_mode=length_mode, **over))
        return [b for row in rows for b in row if b.command.startswith("main_player_length")]

    def test_mixed_is_both_of_them_lit(self):
        assert [b.lit for b in self._pair(LengthMode.MIXED)] == [True, True]

    def test_one_length_is_that_one_lit_and_the_other_dark(self):
        assert [b.lit for b in self._pair(LengthMode.FULL)] == [True, False]
        assert [b.lit for b in self._pair(LengthMode.SHORTS)] == [False, True]

    def test_dropping_one_from_mixed_asks_for_the_other_alone(self):
        assert [b.command for b in self._pair(LengthMode.MIXED)] == [
            "main_player_length_shorts", "main_player_length_full"]

    def test_putting_the_dark_one_back_asks_for_mixed(self):
        assert self._pair(LengthMode.FULL)[1].command == "main_player_length_mixed"

    def test_the_last_lit_one_can_still_be_turned_off(self):
        pair = self._pair(LengthMode.FULL)

        assert [b.dim for b in pair] == [False, False]
        assert pair[0].command == "main_player_length_none"

    def test_neither_lit_offers_each_length_back(self):
        pair = self._pair(LengthMode.NONE)

        assert [b.lit for b in pair] == [False, False]
        assert [b.command for b in pair] == ["main_player_length_full", "main_player_length_shorts"]

    def test_no_pair_at_all_without_a_library_to_filter(self):
        assert self._pair(None) == []


class TestCompilationAndJumps:
    """The set a video belongs to, the scene it came from, and its other cuts."""

    def _button_for(self, action: str, **over):
        return _button(MainSlot(main_mode=MainMode.VIDEO, latest=False, length_mode=LengthMode.MIXED, **over), action)

    def test_the_compilation_button_enters_and_the_lit_one_leaves(self):
        outside = self._button_for("main_player_compilation", has_compilation=True)
        inside = self._button_for("main_player_end_compilation", compilation="Volume 6")

        assert outside.lit is False and outside.dim is False
        assert inside.lit is True

    def test_a_video_in_no_compilation_cannot_be_pressed_into_one(self):
        assert self._button_for("main_player_compilation").dim is True

    def test_inside_a_compilation_the_order_and_length_read_as_held(self):
        rows = console_rows(MainSlot(main_mode=MainMode.VIDEO, latest=False, length_mode=LengthMode.MIXED,
                                     compilation="Volume 6"))
        held = [b for row in rows for b in row
                if b.command in ("main_shuffle", "main_player_length_shorts")]

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
        assert _button(MainSlot(main_mode=MainMode.VIDEO, locked=True), "main_lock").lit is True
        assert _button(MainSlot(main_mode=MainMode.VIDEO, locked=False), "main_lock").lit is False

    def test_it_says_which_way_a_press_goes(self):
        held = _button(MainSlot(main_mode=MainMode.VIDEO, locked=True), "main_lock")
        loose = _button(MainSlot(main_mode=MainMode.VIDEO, locked=False), "main_lock")

        assert held.tooltip.startswith("Locked") and "play on" in held.tooltip
        assert loose.tooltip.startswith("Unlocked") and "hold this video" in loose.tooltip

    def test_it_pairs_with_f_mode_and_stands_apart_from_everything_else(self):
        by_action = _placed(MainSlot(main_mode=MainMode.VIDEO))
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
    """The video's playback rate under a video, Genau's clip pace in genau mode:
    a named row, a read-out the drawing host fills, and the pair around it."""

    def test_the_video_rate_has_controls_where_main_player_is_on_screen(self):
        actions = _actions(MainSlot(main_mode=MainMode.VIDEO))
        assert "main_player_speed_down" in actions and "main_player_speed_up" in actions
        assert "genau_clip_seconds_down" not in actions

    def test_genau_has_the_pace_and_no_video_rate(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))
        assert "genau_clip_seconds_down" in actions and "genau_clip_seconds_up" in actions
        assert "main_player_speed_down" not in actions

    def test_the_rate_is_a_read_out_between_the_arrows_that_the_player_fills(self):
        row = next(row for row in console_rows(MainSlot(main_mode=MainMode.VIDEO))
                   if any(b.command == "main_player_speed_down" for b in row))

        assert [b.glyph or b.host_value for b in row] == [
            "Playback speed", _MINUS, "playback_speed", _PLUS]

    def test_the_seconds_are_a_read_out_between_the_arrows_that_genau_fills(self):
        row = next(row for row in console_rows(MainSlot(main_mode=MainMode.GENAU))
                   if any(b.command == "genau_clip_seconds_down" for b in row))

        assert [b.glyph or b.host_value for b in row] == [
            "Clip seconds", _MINUS, "advance_interval", _PLUS]

    def test_a_read_out_is_not_a_hit_target(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.VIDEO)), x=0, y=0)
        rect = next(r for r, b in placed if b.host_value == "playback_speed")

        assert hit_test(placed, rect[0] + 1, rect[1] + 1) == ""


class TestDriveControls:
    def test_the_switch_row_is_there_in_both_modes(self):
        for mode in MainMode:
            actions = _actions(MainSlot(main_mode=mode))
            for action in ("robot_hand_toggle_cruise", "robot_hand_cycle_shape", "quarter_button",
                           "robot_hand_park", "robot_hand_retract", "robot_hand_release"):
                assert action in actions, (mode, action)

    def test_the_funscript_jump_rides_the_row_under_a_video_alone(self):
        assert "main_player_funscript_jump" in _actions(MainSlot(main_mode=MainMode.VIDEO))
        assert "main_player_funscript_jump" not in _actions(MainSlot(main_mode=MainMode.GENAU))

    def test_the_axis_arrows_are_not_console_buttons(self):
        actions = _actions(MainSlot(main_mode=MainMode.VIDEO))

        for action in ("robot_hand_amplitude_up", "robot_hand_center_down", "robot_hand_speed_up"):
            assert action not in actions

    def test_learned_motion_sits_beside_cruise_and_lights_while_it_has_the_hand(self):
        actions = _actions(MainSlot(main_mode=MainMode.GENAU))

        assert actions.index("robot_hand_toggle_learned") == actions.index("robot_hand_toggle_cruise") + 1
        assert _button(MainSlot(main_mode=MainMode.GENAU), "robot_hand_toggle_learned").glyph == "hi"
        assert _button(MainSlot(main_mode=MainMode.GENAU), "robot_hand_toggle_learned").lit is False
        lit = _button(MainSlot(main_mode=MainMode.GENAU, learned=True), "robot_hand_toggle_learned")
        assert lit.lit is True
        assert (lit.favorite, lit.warn, lit.hold) == (False, False, False)

    def test_cruise_lights_and_the_waveform_names_itself(self):
        slot = MainSlot(main_mode=MainMode.GENAU, cruise=True, shape="rounded_square")

        assert _button(slot, "robot_hand_toggle_cruise").lit is True
        assert _button(slot, "robot_hand_cycle_shape").tooltip == "Waveform: Square"


class TestState:
    def test_the_mode_you_are_in_is_lit_and_the_others_are_not(self):
        slot = MainSlot(main_mode=MainMode.VIDEO)

        assert _button(slot, "main_video_activate").lit is True
        assert _button(slot, "genau_activate").lit is False

    def test_nothing_but_the_recording_and_its_loop_takes_a_color_of_its_own(self):
        colored = [b.command for row in console_rows(MainSlot(main_mode=MainMode.GENAU, cruise=True, locked=True))
                   for b in row if b.warn or b.hold]

        assert colored == []

    def test_f_mode_keeps_the_green_the_other_switches_gave_up(self):
        assert _button(MainSlot(main_mode=MainMode.VIDEO, scripted_filter=True), "main_fmode").favorite is True
        assert _button(MainSlot(main_mode=MainMode.GENAU), "robot_hand_toggle_cruise").favorite is False

    def test_the_record_button_tells_marking_from_looping(self):
        idle = _button(MainSlot(main_mode=MainMode.VIDEO), "main_player_record_tap")
        marking = _button(MainSlot(main_mode=MainMode.VIDEO, loop_state=LoopState.RECORDING), "main_player_record_tap")
        looping = _button(MainSlot(main_mode=MainMode.VIDEO, loop_state=LoopState.LOOPING), "main_player_record_tap")

        assert (idle.warn, idle.hold) == (False, False)
        assert (marking.warn, marking.hold) == (True, False)
        assert (looping.warn, looping.hold) == (False, True)

    def test_the_record_button_says_which_press_comes_next(self):
        for loop_state, wanted in ((LoopState.NORMAL, "Record"), (LoopState.RECORDING, "out point"),
                                   (LoopState.LOOPING, "drop the loop")):
            assert wanted in _button(MainSlot(main_mode=MainMode.VIDEO, loop_state=loop_state),
                                     "main_player_record_tap").tooltip


class TestLayout:
    def test_the_mode_row_leads_so_it_holds_its_place_across_modes(self):
        for mode in MainMode:
            first = console_rows(MainSlot(main_mode=mode))[0]
            assert [b.command for b in first][:3] == [
                "main_video_activate", "genau_activate", "main_minimize"]

    def test_the_file_actions_ride_the_mode_row_where_there_is_a_video(self):
        video = [b.command for b in console_rows(MainSlot(main_mode=MainMode.VIDEO))[0]]
        genau = [b.command for b in console_rows(MainSlot(main_mode=MainMode.GENAU))[0]]

        assert video[3:] == ["browse_library", "main_player_record_tap", "clipper_save"]
        assert genau[3:] == []

    def test_the_file_actions_stand_apart_from_minimize_and_from_each_other(self):
        by_action = _placed(MainSlot(main_mode=MainMode.VIDEO))
        minimize, browse = by_action["main_minimize"], by_action["browse_library"]
        record, save = by_action["main_player_record_tap"], by_action["clipper_save"]

        assert browse[0] - (minimize[0] + minimize[2]) == GROUP_GAP
        assert record[0] - (browse[0] + browse[2]) == GROUP_GAP
        assert save[0] - (record[0] + record[2]) == GAP

    def test_minimize_rides_the_row_that_never_changes(self):
        video, genau = _placed(MainSlot(main_mode=MainMode.VIDEO)), _placed(MainSlot(main_mode=MainMode.GENAU))

        assert video["main_minimize"] == genau["main_minimize"]

    def test_minimize_stands_apart_from_the_modes_it_sits_beside(self):
        by_action = _placed(MainSlot(main_mode=MainMode.VIDEO))
        genau, minimize = by_action["genau_activate"], by_action["main_minimize"]

        assert minimize[0] - (genau[0] + genau[2]) == GROUP_GAP

    def test_minimize_asks_for_a_drawn_bar_rather_than_a_font_glyph(self):
        button = _button(MainSlot(main_mode=MainMode.GENAU), "main_minimize")

        assert button.glyph == MINIMIZE_ICON
        assert "taskbar" in button.tooltip

    def test_a_press_finds_the_button_under_it_and_names_it(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.VIDEO)), x=0, y=0)
        rect = next(r for r, b in placed if b.command == "main_next")

        assert hit_test(placed, rect[0] + 1, rect[1] + 1) == "main_next"
        assert tooltip_at(placed, rect[0] + 1, rect[1] + 1) == "Next video"

    def test_the_buttons_are_the_familys_size(self):
        placed = place_rows(console_rows(MainSlot(main_mode=MainMode.VIDEO)), x=0, y=0)

        assert all(rect[3] == BUTTON for rect, _b in placed)

    def test_the_mode_row_can_be_left_off_and_takes_minimize_with_it(self):
        full = console_rows(MainSlot(main_mode=MainMode.GENAU))
        trimmed = console_rows(MainSlot(main_mode=MainMode.GENAU), modes=False)

        assert trimmed == full[1:]
        actions = [b.command for row in trimmed for b in row]
        assert "main_minimize" not in actions
        assert not any(a.endswith("_activate") for a in actions)
        for kept in ("robot_hand_toggle_cruise", "robot_hand_cycle_shape", "main_lock",
                     "genau_clip_seconds_up", "genau_clip_seconds_down"):
            assert kept in actions


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

    def test_the_bin_takes_something_away(self):
        assert _button(MainSlot(main_mode=MainMode.GENAU), "genau_weird_clip").danger is True
