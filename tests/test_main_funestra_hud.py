from __future__ import annotations

import pytest
from app_support.osr2_device import Osr2Mode, Osr2Trouble
from funestra.hud_button import Button
from funestra.hud_kinds import HudKind, title_filters
from funestra.main_hud import OSR2_PARKED
from funestra.modes import LengthMode, MainMode, Osr2State
from funestra.satellite_hud import HudModel, hud_text, parse_hud
from funestra.satellite_hud_paint import HudRenderer

from fun_time import main_funestra_hud
from fun_time.content import Noun
from fun_time.funestra_status import GenauStatus, MainFunestraStatus
from fun_time.main_funestra_hud import MainFunestraInputs, main_hud_model, osr2_state
from main_funestra.snippet_controller import SnippetState


@pytest.fixture
def flicks_under_a_word_no_code_spells(monkeypatch):
    monkeypatch.setattr(main_funestra_hud, "genau_flick_noun", lambda: Noun(one="pip", many="pips"))


def _payload(**overrides) -> HudModel:
    base = {"main_mode": MainMode.KINO, "active": False, "osr2_mode": Osr2Mode.CONTROLLED,
                "main_funestra": MainFunestraStatus(), "genau": GenauStatus()}
    base.update(overrides)
    return main_hud_model(MainFunestraInputs(**base))


def _button(model: HudModel, action: str) -> Button:
    return next(b for row in model.rows for b in row if b.command == action)


class TestOsr2State:
    def test_the_devices_own_modes_answer_whatever_is_playing(self):
        for osr2_mode, expected in ((Osr2Mode.OFF, Osr2State.OFF),
                                    (Osr2Mode.AUTO, Osr2State.AUTO)):
            assert osr2_state(main_mode=MainMode.KINO, osr2_mode=osr2_mode,
                              funscript_driving=True) == expected

    def test_a_funscript_that_is_actually_driving_says_so(self):
        assert osr2_state(main_mode=MainMode.KINO, osr2_mode=Osr2Mode.CONTROLLED,
                          funscript_driving=True) == Osr2State.FUNSCRIPT

    def test_a_scripted_videos_quiet_stretch_reads_as_the_robot_hand_not_funscript(self):
        assert osr2_state(main_mode=MainMode.KINO, osr2_mode=Osr2Mode.CONTROLLED,
                          funscript_driving=False) == Osr2State.ROBOT_HAND

    def test_without_a_driver_the_robot_hand_has_the_device_in_either_mode(self):
        for mode in MainMode:
            assert osr2_state(main_mode=mode, osr2_mode=Osr2Mode.CONTROLLED,
                              funscript_driving=False) == Osr2State.ROBOT_HAND

    def test_an_osr2_that_cannot_be_heard_is_named_for_why(self):
        for trouble, expected in ((Osr2Trouble.NOT_FOUND, Osr2State.UNPLUGGED),
                                  (Osr2Trouble.NOT_ANSWERING, Osr2State.NO_ANSWER)):
            assert osr2_state(main_mode=MainMode.KINO, osr2_mode=Osr2Mode.OFF,
                              funscript_driving=True, trouble=trouble) == expected

    def test_a_parked_main_funestras_stale_status_cannot_claim_the_device(self):
        assert osr2_state(main_mode=MainMode.GENAU, osr2_mode=Osr2Mode.CONTROLLED,
                          funscript_driving=True) == Osr2State.ROBOT_HAND


class TestPayload:
    def test_carries_the_room_the_funestra_cannot_see(self):
        payload = _payload(main_mode=MainMode.KINO, active=True, osr2_mode=Osr2Mode.AUTO)

        assert payload.kind is HudKind.MAIN
        assert payload.facts.main_mode == "kino"
        assert payload.active is True
        assert payload.osr2 == Osr2State.AUTO

    def test_carries_why_the_osr2_cannot_be_heard(self):
        payload = _payload(osr2_mode=Osr2Mode.OFF, osr2_trouble=Osr2Trouble.NOT_ANSWERING)

        assert payload.osr2 == Osr2State.NO_ANSWER

    def test_the_osr2_state_is_read_off_the_main_funestras_own_funscript(self):
        driving = MainFunestraStatus(has_funscript=True)

        assert _payload(main_mode=MainMode.KINO, main_funestra=driving).osr2 == Osr2State.FUNSCRIPT
        assert _payload(main_mode=MainMode.GENAU, main_funestra=driving).osr2 == Osr2State.ROBOT_HAND

    def test_the_record_button_says_which_press_comes_next(self):
        recording = _button(_payload(main_funestra=MainFunestraStatus(snippet_state=SnippetState.RECORDING)),
                            "main_funestra_record_tap")
        resting = _button(_payload(), "main_funestra_record_tap")

        assert recording.warn
        assert "out point" in recording.tooltip
        assert not resting.warn
        assert not resting.hold

    def test_the_lock_reported_is_the_lock_of_whoever_is_showing(self):
        held_flick, loose_flick = GenauStatus(locked=True), GenauStatus(locked=False)
        held_video, loose_video = MainFunestraStatus(locked=True), MainFunestraStatus(locked=False)

        for mode, main_funestra, genau, expected in (
            (MainMode.KINO, held_video, loose_flick, True),
            (MainMode.KINO, loose_video, held_flick, False),
            (MainMode.GENAU, loose_video, held_flick, True),
            (MainMode.GENAU, held_video, loose_flick, False),
        ):
            payload = _payload(main_mode=mode, main_funestra=main_funestra, genau=genau)
            assert payload.facts.locked is expected
            assert _button(payload, "main_lock").lit is expected

    def test_the_flip_button_lights_for_the_video_genau_says_is_flipped(self):
        flipped = _payload(main_mode=MainMode.GENAU, genau=GenauStatus(flipped=True))

        assert _button(flipped, "genau_flip_ends").lit is True
        assert _button(_payload(main_mode=MainMode.GENAU), "genau_flip_ends").lit is False

    def test_genaus_pace_is_named_on_its_lock(self):
        payload = _payload(main_mode=MainMode.GENAU, genau=GenauStatus(locked=False), genau_pace_s=7)

        assert "every 7s" in _button(payload, "main_lock").tooltip

    def test_declares_what_the_main_funestra_published_about_its_video(self):
        known = _payload(main_funestra=MainFunestraStatus(
            compilation="Vol 3", has_compilation=True, has_other_versions=True, jump_to="scene"))
        unknown = _payload()

        assert _button(known, "main_funestra_end_compilation").lit is True
        assert _button(unknown, "main_funestra_compilation").dim is True
        assert _button(known, "main_funestra_cycle_version").dim is False
        assert _button(unknown, "main_funestra_cycle_version").dim is True
        assert _button(known, "main_funestra_full_vid").dim is False
        assert _button(unknown, "main_funestra_clip_jump").dim is True


def test_the_hud_alone_tells_the_main_funestra_its_browse_order():
    assert _payload(latest=True).facts.latest is True
    assert _payload().facts.latest is False
    assert _button(_payload(latest=True), "main_latest").lit is True
    assert _button(_payload(), "main_shuffle").lit is True


def test_f_mode_lights_off_what_the_orchestrator_holds():
    assert _button(_payload(scripted_filter=True), "main_fmode").lit is True
    assert _button(_payload(), "main_fmode").lit is False


def test_in_genau_mode_f_mode_lights_off_genaus_own_and_not_kinos():
    assert _button(_payload(main_mode=MainMode.GENAU, genau_favorites_filter=True), "main_fmode").lit is True
    assert _button(_payload(main_mode=MainMode.GENAU, scripted_filter=True), "main_fmode").lit is False


def test_the_length_pair_lights_off_the_narrowing_of_fun_times_own_list():
    lit = [button.lit for row in _payload(length_mode=LengthMode.CLIPS).rows for button in row
           if button.command.startswith("main_funestra_length")]

    assert lit == [True, False]


def test_the_order_reported_is_the_order_of_whoever_is_showing():
    assert _payload(main_mode=MainMode.KINO, latest=True, genau_latest=False).facts.latest is True
    assert _payload(main_mode=MainMode.KINO, latest=False, genau_latest=True).facts.latest is False

    assert _payload(main_mode=MainMode.GENAU, latest=False, genau_latest=True).facts.latest is True
    assert _payload(main_mode=MainMode.GENAU, latest=True, genau_latest=False).facts.latest is False


def test_outside_the_headset_there_is_one_shape_and_its_pair_is_dimmed():
    one_shape_and_so_no_choice = {"plays_vr": None, "plays_flat": None}
    shapes = [button for row in _payload(**one_shape_and_so_no_choice).rows for button in row
              if button.command.startswith("main_projection")]

    assert shapes
    assert all(button.dim for button in shapes)


def test_the_hud_says_which_shapes_of_video_the_browse_may_reach():
    headset = _payload(plays_vr=True, plays_flat=False)
    assert _button(headset, "main_projection_none").lit is True
    assert _button(headset, "main_projection_both").lit is False


def test_the_shapes_reported_are_the_shapes_of_whoever_is_showing():
    both = {"plays_vr": True, "plays_flat": True, "genau_plays_vr": False, "genau_plays_flat": True}

    assert _button(_payload(main_mode=MainMode.KINO, **both), "main_projection_flat").lit is True
    assert _button(_payload(main_mode=MainMode.GENAU, **both), "main_projection_both").lit is False
    assert _button(_payload(main_mode=MainMode.GENAU, **both), "main_projection_none").lit is True


def test_the_hud_title_names_each_pair_left_on_one_satellite_in_the_order_they_sit():
    assert title_filters(_payload().facts) == ()
    assert title_filters(_payload(length_mode=LengthMode.CLIPS).facts) == ("Clips",)
    assert title_filters(_payload(plays_vr=True, plays_flat=False,
                    length_mode=LengthMode.FULL).facts) == ("VR", "Full")


def test_the_hud_title_in_genau_mode_names_genaus_own_narrowing():
    narrowed = {"plays_vr": True, "plays_flat": False, "length_mode": LengthMode.CLIPS,
                    "genau_plays_vr": False, "genau_plays_flat": True}

    assert title_filters(_payload(main_mode=MainMode.GENAU, **narrowed).facts) == ("2D",)


def test_the_hud_title_names_the_orientation_last_in_either_mode():
    assert title_filters(_payload(length_mode=LengthMode.CLIPS, plays_landscape=False).facts) == (
        "Clips", "Portrait")
    assert title_filters(_payload(main_mode=MainMode.GENAU, plays_landscape=False,
                    genau_plays_portrait=False).facts) == ("Landscape",)


def test_the_published_text_carries_the_facts_the_funestra_builds_its_hud_from():
    payload = _payload(main_mode=MainMode.GENAU)

    parsed = parse_hud(hud_text(payload))

    assert parsed == payload
    assert parsed.facts == payload.facts
    assert parsed.rows


def test_the_buttons_it_declares_are_the_ones_the_funestra_presses():
    drawn = HudRenderer("main").render(parse_hud(hud_text(_payload(main_mode=MainMode.KINO))))

    assert "main_next" in [button.command for _rect, button in drawn.targets.buttons]


def test_the_crown_on_the_main_hud_is_lit_while_the_room_has_given_it_to_the_main_funestra():
    assert _button(_payload(crowned=True), "main_crown").lit is True
    assert _button(_payload(crowned=False), "main_crown").lit is False


def test_genau_names_its_flicks_in_the_overlays_word(flicks_under_a_word_no_code_spells):
    facts = _payload(main_mode=MainMode.GENAU).facts

    assert (facts.flick_one, facts.flick_many) == ("pip", "pips")


def test_the_main_hud_says_whether_what_it_shows_is_a_favorite():
    assert _payload(is_favorite=True).is_favorite is True
    assert _payload().is_favorite is False


def test_it_carries_the_osr2s_control_and_the_pause_the_room_holds():
    payload = _payload(osr2_control=OSR2_PARKED, paused=True)

    assert (payload.osr2_control, payload.paused) == (OSR2_PARKED, True)


def test_it_tells_the_funestra_what_the_main_funestra_said_of_its_video():
    facts = _payload(main_funestra=MainFunestraStatus(
        image=True, duration_ms=60_000, has_funscript=True,
        snippet_state=SnippetState.REPEATING)).facts

    assert (facts.image, facts.longer_than_a_step, facts.has_funscript, facts.snippet) == (
        True, True, True, "repeating")
