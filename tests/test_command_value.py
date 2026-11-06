from __future__ import annotations

import pytest

from fun_time.command_value import Command, Scope, parse_command
from fun_time.event_log import SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT, SOURCE_SYSTEM
from fun_time.fun_time_funestras import FunTimeFunestra


class TestParsingWhatWasSaid:
    @pytest.mark.parametrize(("text", "said_to", "action", "argument"), [
        ("quit", Scope.SESSION, "quit", ""),
        ("main_next", Scope.SESSION, "main_next", ""),
        ("portrait_next", Scope.PORTRAIT, "next", ""),
        ("landscape_lock_on", Scope.LANDSCAPE, "lock_on", ""),
        ("both_trash", Scope.BOTH, "trash", ""),
        ("all_reset", Scope.ALL, "reset", ""),
        ("active_cycle_action", Scope.ACTIVE, "cycle_action", ""),
        ("portrait_nav_up", Scope.PORTRAIT, "nav_up", ""),
        ("portrait_hud_left", Scope.PORTRAIT, "hud_left", ""),
    ])
    def test_a_funestra_word_in_front_says_whom_it_is_said_to(self, text, said_to, action, argument):
        assert parse_command(text) == Command(said_to, action, argument, text)

    @pytest.mark.parametrize(("text", "said_to", "action", "argument"), [
        ("portrait_speed_75", Scope.PORTRAIT, "speed", "75"),
        ("main_funestra_speed_125", Scope.SESSION, "main_funestra_speed", "125"),
        ("robot_hand_amp_50", Scope.SESSION, "robot_hand_amp", "50"),
        ("genau_flick_seconds_7", Scope.SESSION, "genau_flick_seconds", "7"),
        ("max_intensity_40", Scope.SESSION, "max_intensity", "40"),
        ("max_intensity_-5", Scope.SESSION, "max_intensity", "-5"),
    ])
    def test_a_number_on_the_end_is_the_commands_value(self, text, said_to, action, argument):
        assert parse_command(text) == Command(said_to, action, argument, text)

    @pytest.mark.parametrize("text", ["portrait_speed_up", "robot_hand_speed_down",
                                      "portrait_take_osr2", "genau_flick_seconds_up"])
    def test_a_word_on_the_end_is_part_of_the_action(self, text):
        assert parse_command(text).argument == ""

    @pytest.mark.parametrize(("text", "said_to", "action", "argument"), [
        ("portrait_play_video|C:/v/scene two.mp4", Scope.PORTRAIT, "play_video", "C:/v/scene two.mp4"),
        ("landscape_lock_video|C:/v/a_b.mp4", Scope.LANDSCAPE, "lock_video", "C:/v/a_b.mp4"),
        ("portrait_hud_restore_at|upper_left", Scope.PORTRAIT, "hud_restore_at", "upper_left"),
        ("main_hud_restore_at|right", Scope.SESSION, "main_hud_restore_at", "right"),
        ("audio_set_volume|40", Scope.SESSION, "audio_set_volume", "40"),
    ])
    def test_what_follows_a_bar_is_the_commands_value(self, text, said_to, action, argument):
        assert parse_command(text) == Command(said_to, action, argument, text)

    def test_words_for_the_hosted_show_are_the_value_of_say(self):
        assert parse_command("portrait_say_fix_teeth") == Command(
            Scope.PORTRAIT, "say", "fix teeth", "portrait_say_fix_teeth")

    @pytest.mark.parametrize(("text", "said_to", "query"), [
        ("filter_both_beta_gamma", Scope.BOTH, "beta gamma"),
        ("filter_portrait_alpha", Scope.PORTRAIT, "alpha"),
        ("filter_landscape_delta", Scope.LANDSCAPE, "delta"),
    ])
    def test_a_filter_names_its_scope_after_the_word_filter(self, text, said_to, query):
        assert parse_command(text) == Command(said_to, "filter", query, text)

    @pytest.mark.parametrize("text", ["filter_nowhere_x", "filterish", "filter_"])
    def test_a_filter_said_to_nobody_is_no_filter(self, text):
        assert parse_command(text).action != "filter"

    def test_the_value_keeps_its_own_spelling(self):
        assert parse_command("portrait_next").text == "portrait_next"
        assert parse_command("portrait_say_fix_teeth").text == "portrait_say_fix_teeth"


class TestWhichFunestraItAddresses:
    @pytest.mark.parametrize(("text", "funestra"), [
        ("portrait_next", FunTimeFunestra.PORTRAIT),
        ("landscape_next", FunTimeFunestra.LANDSCAPE),
        ("filter_landscape_alpha", None),
        ("main_funestra_speed_up", FunTimeFunestra.MAIN),
        ("main_next", FunTimeFunestra.MAIN),
        ("genau_pause", FunTimeFunestra.MAIN),
        ("omnipause_toggle", None),
        ("active_next", None),
        ("both_next", None),
        ("filter_both_alpha", None),
    ])
    def test_a_command_addresses_the_funestra_it_names_or_none(self, text, funestra):
        assert parse_command(text).funestra is funestra

    @pytest.mark.parametrize(("text", "satellite"), [
        ("portrait_next", FunTimeFunestra.PORTRAIT),
        ("filter_landscape_alpha", FunTimeFunestra.LANDSCAPE),
        ("landscape_say_fix_teeth", FunTimeFunestra.LANDSCAPE),
        ("main_next", None),
        ("both_next", None),
        ("filter_both_alpha", None),
    ])
    def test_a_filter_is_said_to_its_satellite_though_it_addresses_no_funestra(self, text, satellite):
        assert parse_command(text).satellite is satellite

    @pytest.mark.parametrize(("text", "active", "source"), [
        ("portrait_next", FunTimeFunestra.MAIN, SOURCE_PORTRAIT),
        ("landscape_next", None, SOURCE_LANDSCAPE),
        ("main_funestra_speed_up", None, SOURCE_MAIN),
        ("active_next", FunTimeFunestra.LANDSCAPE, SOURCE_LANDSCAPE),
        ("active_next", None, SOURCE_SYSTEM),
        ("omnipause_toggle", FunTimeFunestra.PORTRAIT, SOURCE_SYSTEM),
    ])
    def test_a_message_is_about_the_funestra_addressed_else_the_whole_session(
            self, text, active, source):
        assert parse_command(text).message_source(active) == source


class TestAimingAtTheActiveFunestra:
    def test_a_command_that_is_not_active_is_left_as_it_was_said(self):
        command = parse_command("portrait_next")

        assert command.aimed_at_the_active(FunTimeFunestra.LANDSCAPE) is command

    def test_an_active_command_goes_to_the_satellite_that_is_active(self):
        assert parse_command("active_next").aimed_at_the_active(FunTimeFunestra.PORTRAIT) == (
            parse_command("portrait_next"))
        assert parse_command("active_lock_action").aimed_at_the_active(FunTimeFunestra.LANDSCAPE) == (
            parse_command("landscape_lock_action"))

    def test_an_active_command_on_the_main_funestra_takes_the_main_funestras_own_form(self):
        assert parse_command("active_reset").aimed_at_the_active(FunTimeFunestra.MAIN) == (
            parse_command("main_reset"))
        assert parse_command("active_trash").aimed_at_the_active(FunTimeFunestra.MAIN) == (
            parse_command("genau_trash_flick"))
        assert parse_command("active_speed_up").aimed_at_the_active(FunTimeFunestra.MAIN) == (
            parse_command("main_funestra_speed_up"))
        assert parse_command("active_speed_75").aimed_at_the_active(FunTimeFunestra.MAIN) == (
            parse_command("main_funestra_speed_75"))

    def test_an_active_command_the_main_funestra_has_no_form_of_stays_active(self):
        command = parse_command("active_no_such_thing")

        assert command.aimed_at_the_active(FunTimeFunestra.MAIN) is command


class TestOneCommandForEachFunestraOfAGroup:
    def test_a_command_to_one_funestra_is_itself(self):
        assert parse_command("portrait_next").each() == (parse_command("portrait_next"),)
        assert parse_command("quit").each() == (parse_command("quit"),)

    def test_both_is_the_portrait_then_the_landscape_funestra(self):
        assert parse_command("both_next").each() == (
            parse_command("portrait_next"), parse_command("landscape_next"))
        assert parse_command("both_lock_on").each() == (
            parse_command("portrait_lock_on"), parse_command("landscape_lock_on"))

    def test_all_reaches_the_main_funestra_and_both_satellites(self):
        assert parse_command("all_reset").each() == (
            parse_command("main_reset"), parse_command("portrait_reset"),
            parse_command("landscape_reset"))

    def test_a_filter_said_to_both_shows_stays_one_command(self):
        assert parse_command("filter_both_alpha").each() == (parse_command("filter_both_alpha"),)

    def test_a_filter_said_to_both_shows_has_a_half_for_each_satellite(self):
        assert parse_command("filter_both_beta_gamma").halves() == (
            parse_command("filter_portrait_beta_gamma"),
            parse_command("filter_landscape_beta_gamma"))
        assert parse_command("filter_portrait_alpha").halves() == (
            parse_command("filter_portrait_alpha"),)
        assert parse_command("portrait_next").halves() == (parse_command("portrait_next"),)


class TestTheKeyARouteIsBoundUnder:
    def test_a_satellites_command_is_keyed_by_the_satellite_and_its_action(self):
        assert parse_command("portrait_speed_75").key == (Scope.PORTRAIT, "speed")
        assert parse_command("landscape_play_video|C:/v.mp4").key == (Scope.LANDSCAPE, "play_video")

    def test_words_said_to_a_show_are_keyed_by_the_words_themselves(self):
        assert parse_command("portrait_say_favorites").key == (Scope.PORTRAIT, "say favorites")
        assert parse_command("portrait_say_fix_teeth").key != parse_command("portrait_say_favorites").key

    def test_a_sessions_command_is_keyed_by_its_whole_name_short_of_its_value(self):
        assert parse_command("quit").key == (Scope.SESSION, "quit")
        assert parse_command("main_funestra_speed_75").key == (Scope.SESSION, "main_funestra_speed")

    def test_a_command_said_to_a_satellite_is_spelled_from_the_satellite_and_its_action(self):
        assert Command.to(FunTimeFunestra.PORTRAIT, "lock_on") == parse_command("portrait_lock_on")
        assert Command.to(FunTimeFunestra.LANDSCAPE, "lock_off").text == "landscape_lock_off"


class TestResolveActiveSideCommand:
    def test_group_commands_ride_the_same_active_and_both_plumbing(self):
        assert parse_command("active_action_loop").aimed_at_the_active(3).text == "landscape_action_loop"
        assert parse_command("active_lock_action").aimed_at_the_active(2).text == "portrait_lock_action"
        assert [each.text for each in parse_command("both_seed_loop").each()] == ["portrait_seed_loop", "landscape_seed_loop"]

    def test_rewrites_to_portrait_when_active_funestra_is_portrait(self):
        assert parse_command("active_lock_on").aimed_at_the_active(2).text == "portrait_lock_on"

    def test_rewrites_to_landscape_when_active_funestra_is_landscape(self):
        assert parse_command("active_next").aimed_at_the_active(3).text == "landscape_next"

    def test_passes_non_active_commands_through(self):
        assert parse_command("main_next").aimed_at_the_active(3).text == "main_next"
        assert parse_command("portrait_lock").aimed_at_the_active(3).text == "portrait_lock"

    def test_active_nav_targets_primary_when_primary_is_active(self):
        assert parse_command("active_next").aimed_at_the_active(1).text == "main_next"
        assert parse_command("active_prev").aimed_at_the_active(1).text == "main_prev"

    def test_the_funscript_moves_follow_the_funestra_last_navigated_the_main_under_its_own_ids(self):
        for action, main in (("funscript_jump", "main_funestra_funscript_jump"),
                             ("next_funscripted", "main_funestra_next_funscripted")):
            assert parse_command(f"active_{action}").aimed_at_the_active(1).text == main
            assert parse_command(f"active_{action}").aimed_at_the_active(2).text == f"portrait_{action}"
            assert parse_command(f"active_{action}").aimed_at_the_active(3).text == f"landscape_{action}"
            assert [each.text for each in parse_command(f"both_{action}").each()] == [
                f"portrait_{action}", f"landscape_{action}"]

    def test_reset_on_the_primary_means_the_main_funestras_own_reset(self):
        assert parse_command("active_reset").aimed_at_the_active(1).text == "main_reset"
        assert parse_command("active_reset").aimed_at_the_active(2).text == "portrait_reset"
        assert parse_command("active_reset").aimed_at_the_active(3).text == "landscape_reset"

    def test_end_loop_on_the_primary_means_main_funestras_own_loop(self):
        assert parse_command("active_no_loop").aimed_at_the_active(1).text == "main_funestra_loop_cancel"
        assert parse_command("active_no_loop").aimed_at_the_active(2).text == "portrait_no_loop"
        assert parse_command("active_no_loop").aimed_at_the_active(3).text == "landscape_no_loop"

    def test_a_bare_lock_reaches_the_primary_too(self):
        assert parse_command("active_lock_on").aimed_at_the_active(1).text == "main_lock_on"
        assert parse_command("active_lock_off").aimed_at_the_active(1).text == "main_lock_off"
        assert parse_command("active_lock_on").aimed_at_the_active(2).text == "portrait_lock_on"
        assert parse_command("active_lock_off").aimed_at_the_active(3).text == "landscape_lock_off"

    def test_a_bare_f_mode_reaches_whichever_funestra_is_active(self):
        for suffix in ("", "_on", "_off"):
            assert parse_command(f"active_fmode{suffix}").aimed_at_the_active(1).text == f"main_fmode{suffix}"
            assert parse_command(f"active_fmode{suffix}").aimed_at_the_active(2).text == f"portrait_fmode{suffix}"
            assert parse_command(f"active_fmode{suffix}").aimed_at_the_active(3).text == f"landscape_fmode{suffix}"

    def test_a_bare_browse_order_reaches_whichever_funestra_is_active(self):
        for order in ("latest", "shuffle"):
            assert parse_command(f"active_{order}").aimed_at_the_active(1).text == f"main_{order}"
            assert parse_command(f"active_{order}").aimed_at_the_active(2).text == f"portrait_{order}"
            assert parse_command(f"active_{order}").aimed_at_the_active(3).text == f"landscape_{order}"

    def test_a_bare_skip_or_back_reaches_whichever_funestra_is_active(self):
        for action in ("nudge_next", "nudge_prev"):
            assert parse_command(f"active_{action}").aimed_at_the_active(1).text == f"main_{action}"
            assert parse_command(f"active_{action}").aimed_at_the_active(2).text == f"portrait_{action}"
            assert parse_command(f"active_{action}").aimed_at_the_active(3).text == f"landscape_{action}"

    def test_a_bare_playback_speed_reaches_whichever_funestra_is_active(self):
        for action in ("speed_up", "speed_down", "speed_150"):
            assert parse_command(f"active_{action}").aimed_at_the_active(1).text == f"main_funestra_{action}"
            assert parse_command(f"active_{action}").aimed_at_the_active(2).text == f"portrait_{action}"
            assert parse_command(f"active_{action}").aimed_at_the_active(3).text == f"landscape_{action}"

    def test_active_satellite_only_command_is_noop_when_primary_is_active(self):
        assert parse_command("active_cycle_seed").aimed_at_the_active(1).text == "active_cycle_seed"

    def test_a_bare_trash_on_the_main_funestra_reaches_genaus_item(self):
        assert parse_command("active_trash").aimed_at_the_active(FunTimeFunestra.MAIN).text == "genau_trash_flick"


class TestExpandGroupCommand:
    def test_passes_through_non_both_commands(self):
        assert [each.text for each in parse_command("portrait_next").each()] == ["portrait_next"]
        assert [each.text for each in parse_command("quit").each()] == ["quit"]

    def test_expands_to_portrait_then_landscape(self):
        assert [each.text for each in parse_command("both_next").each()] == ["portrait_next", "landscape_next"]
        assert [each.text for each in parse_command("both_prev").each()] == ["portrait_prev", "landscape_prev"]
        assert [each.text for each in parse_command("both_trash").each()] == ["portrait_trash", "landscape_trash"]

    def test_expands_multiword_suffixes(self):
        assert [each.text for each in parse_command("both_lock_on").each()] == ["portrait_lock_on", "landscape_lock_on"]
        assert [each.text for each in parse_command("both_lock_off").each()] == ["portrait_lock_off", "landscape_lock_off"]
        assert [each.text for each in parse_command("both_cycle_action").each()] == [
            "portrait_cycle_action", "landscape_cycle_action",
        ]
        assert [each.text for each in parse_command("both_cycle_seed").each()] == [
            "portrait_cycle_seed", "landscape_cycle_seed",
        ]

    def test_reset_all_reaches_the_main_funestra_and_both_satellites(self):
        assert [each.text for each in parse_command("all_reset").each()] == [
            "main_reset", "portrait_reset", "landscape_reset",
        ]
