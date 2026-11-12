from __future__ import annotations

import pytest

from fun_time.command_value import FILTER, SAY, parse_command, say_command
from fun_time.content import Noun, genau_flick_noun
from fun_time.filter_vocab import (
    filter_voice_commands,
    set_command,
)
from fun_time.voice_commands import (
    VOICE_COMMANDS,
    CommandLine,
    build_voice_commands,
    format_spoken_command,
    friendly_voice,
    parse_command_line,
)


def test_the_words_said_to_a_satellite_are_read_back_off_the_command_they_were_written_as():
    said = parse_command(say_command("landscape", "fix alpha hands"))
    assert (said.action, said.said_to, said.argument) == (SAY, "landscape", "fix alpha hands")
    assert parse_command("landscape_next").action != SAY


SATELLITE_WORDS = ("portrait", "landscape", "satellites")
FUNESTRAS = (*SATELLITE_WORDS, "main")


class TestCommandLineFormat:
    def test_round_trips_the_utterance_start_and_what_was_said(self):
        line = format_spoken_command("portrait_lock_on", spoken_at=1234.5, said="lock portrait")
        assert parse_command_line(line) == CommandLine("portrait_lock_on", 1234.5, "lock portrait")

    def test_an_unstamped_line_is_a_bare_command(self):
        assert parse_command_line("portrait_next") == CommandLine("portrait_next")

    def test_a_command_ending_in_an_unparseable_stamp_stays_whole(self):
        assert parse_command_line("filter_satellites_come @ shot") == CommandLine("filter_satellites_come @ shot")


class TestTheSpokenCrossing:
    @pytest.mark.parametrize(
        ("phrase", "command"),
        [
            ("enter vr", "enter_vr"),
            ("enter v r", "enter_vr"),
            ("exit vr", "exit_vr"),
            ("exit v r", "exit_vr"),
        ],
    )
    def test_vr_answers_as_the_word_and_as_its_two_letters(self, phrase, command):
        assert VOICE_COMMANDS[phrase] == command

    def test_no_shorter_phrase_can_outscore_a_crossing_it_starts(self):
        crossings = [p for p, c in VOICE_COMMANDS.items() if c in ("enter_vr", "exit_vr")]
        assert crossings
        for phrase in crossings:
            first, _, rest = phrase.partition(" ")
            assert rest, phrase
            assert first not in VOICE_COMMANDS, (
                f"{first!r} is a command on its own, so {phrase!r} will lose to it"
            )

    @pytest.mark.parametrize(
        ("phrase", "shown"),
        [("enter v r", "enter VR"), ("enter vr", "enter VR"),
         ("exit v r", "exit VR"), ("exit vr", "exit VR")],
    )
    def test_the_reference_and_the_messages_show_one_spelling(self, phrase, shown):
        assert friendly_voice(phrase) == shown


class TestTheVrReset:
    @pytest.mark.parametrize("phrase", ["vr reset", "v r reset"])
    def test_both_spellings_put_the_funestras_back_and_read_as_vr_reset(self, phrase):
        assert VOICE_COMMANDS[phrase] == "vr_reset"
        assert friendly_voice(phrase) == "VR reset"


_SAID_INSTEAD_OF_A_WORD_VOSK_LACKS = {
    "upscales": "enhanced only",
    "unpause": "un pause",
    "unpark": "un park",
    "funscript": "fun script",
    "omnipause": "omni pause",
    "omniplay": "omni play",
    "osr2": "o s r two",
    "2d": "two d",
    "hotkeys": "hot keys",
    "origenerator": "aura generator",
}


class TestVoiceCommands:
    def test_quit_quits_and_no_bare_exit_shadows_exit_vr(self):
        assert VOICE_COMMANDS["quit"] == "quit"
        assert "exit" not in VOICE_COMMANDS
        assert VOICE_COMMANDS["exit vr"] == "exit_vr"

    def test_omni_pause_gives_the_recognizer_three_syllables_for_pause(self):
        assert VOICE_COMMANDS["pause"] == "pause"
        assert VOICE_COMMANDS["omni pause"] == "pause"
        assert VOICE_COMMANDS["omni play"] == "play"

    def test_nothing_said_to_a_muted_recognizer_turns_voice_back_on(self):
        assert VOICE_COMMANDS["voice off"] == VOICE_COMMANDS["mic off"] == "voice_off"
        assert not {"voice_on", "voice_toggle"} & set(VOICE_COMMANDS.values())

    def test_contains_all_static_phrases(self):
        static_phrases = {
            "quit": "quit",
            "pause": "pause",
            "play": "play",
            "go now": "genau_activate",
            "go now mode": "genau_activate",
            "kino": "kino_activate",
            "kino mode": "kino_activate",
            "main kino mode": "main_kino_activate",
            "start broker": "broker_start",
            "stop broker": "broker_stop",
            "next main": "main_next",
            "previous main": "main_prev",
            "skip": "active_nudge_next",
            "back": "active_nudge_prev",
            "record": "main_funestra_record_down",
            "snippet": "main_funestra_record_up",
            "end loop": "active_no_loop",
            "version": "main_funestra_cycle_version",
            "clips only": "main_funestra_length_clips",
            "full only": "main_funestra_length_full",
            "browse": "browse_library",
            "save snippet": "genaumacher_save",
            "cycle projection": "projection_cycle",
            "vr and two d": "main_projection_both",
            "two d and vr": "main_projection_both",
            "fun scripts": "main_fmode_on",
            "main fun scripts": "main_fmode_on",
            "fun scripts main": "main_fmode_on",
            "slow down": "speed_down",
            "speed down": "speed_down",
            "speed up": "speed_up",
            "playback slow down": "active_speed_down",
            "playback speed down": "active_speed_down",
            "playback speed up": "active_speed_up",
            "amp down": "robot_hand_amplitude_down",
            "amp up": "robot_hand_amplitude_up",
            "center down": "robot_hand_center_down",
            "center up": "robot_hand_center_up",
            "next shape": "robot_hand_cycle_shape",
            "previous shape": "robot_hand_cycle_shape_prev",
            "human inspired": "robot_hand_toggle_learned",
            "human inspired on": "robot_hand_learned_on",
            "human inspired off": "robot_hand_learned_off",
            "voice off": "voice_off",
            "mic off": "voice_off",
        }
        for phrase, cmd in static_phrases.items():
            assert VOICE_COMMANDS[phrase] == cmd

    def test_genaus_flicks_are_said_in_the_overlays_word(self):
        said = build_voice_commands(genau_flick_words=Noun(one="pip", many="pips"))

        assert {phrase: said[phrase] for phrase in ("previous pip", "next pip", "save pip")} == {
            "previous pip": "genau_prev_flick",
            "next pip": "genau_next_flick",
            "save pip": "genaumacher_save",
        }
        assert [phrase for phrase, command in said.items()
                if command.startswith("genau") and "clip" in phrase.split()] == []
        assert "clip" not in VOICE_COMMANDS
        assert "save clip" not in VOICE_COMMANDS

    def test_marking_a_flick_trash_names_it_where_bare_trash_means_the_active_satellite(self):
        said = build_voice_commands(genau_flick_words=Noun(one="pip", many="pips"))

        assert (said["trash"], said["main trash"]) == ("active_trash", "genau_trash_flick")

    def test_the_overlay_says_the_words_that_flip_genaus_flick(self):
        assert build_voice_commands(flick_flip_phrases=("flip alpha",))["flip alpha"] == "genau_flip_ends"

    def test_genaus_flick_has_no_second_lock_to_disagree_with_the_main_one(self):
        assert "lock clip" not in VOICE_COMMANDS
        assert "auto advance" not in VOICE_COMMANDS
        assert "advance on" not in VOICE_COMMANDS
        assert "advance off" not in VOICE_COMMANDS
        assert VOICE_COMMANDS["main lock"] == "main_lock_on"

    def test_a_spoken_interval_names_the_seconds_a_flick_holds_the_screen(self):
        said = build_voice_commands(genau_flick_words=Noun(one="pip", many="pips"))

        for word, seconds in (
            ("one", 1), ("five", 5), ("nine", 9), ("fifteen", 15),
            ("thirty", 30), ("forty five", 45), ("sixty", 60),
        ):
            assert said[f"pip seconds {word}"] == f"genau_flick_seconds_{seconds}"
        assert not any(p.startswith("auto advance") for p in said)

    def test_zero_seconds_would_step_the_flick_every_frame_and_is_unsayable(self):
        assert not any(cmd == "genau_flick_seconds_0" for cmd in VOICE_COMMANDS.values())

    def test_the_sound_is_said_of_the_funestra_last_addressed_or_of_the_one_named(self):
        assert VOICE_COMMANDS["mute"] == "active_mute"
        assert VOICE_COMMANDS["un mute"] == "active_unmute"
        assert VOICE_COMMANDS["quiet"] == VOICE_COMMANDS["quieter"] == "active_volume_down"
        assert VOICE_COMMANDS["loud"] == VOICE_COMMANDS["louder"] == "active_volume_up"
        assert VOICE_COMMANDS["portrait mute"] == VOICE_COMMANDS["mute portrait"] == "portrait_mute"
        assert VOICE_COMMANDS["landscape louder"] == "landscape_volume_up"
        assert VOICE_COMMANDS["satellites quiet"] == "satellites_volume_down"
        assert VOICE_COMMANDS["main mute"] == "audio_mute"
        assert VOICE_COMMANDS["un mute main"] == "audio_unmute"
        assert VOICE_COMMANDS["main louder"] == "audio_volume_up"

    def test_the_browser_opens_by_its_other_names_too(self):
        for phrase in ("browse", "open browser", "open file browser", "file browser", "file browse"):
            assert VOICE_COMMANDS[phrase] == "browse_library", phrase

    def test_human_inspired_motion_answers_to_its_initials(self):
        assert VOICE_COMMANDS["hi on"] == "robot_hand_learned_on"
        assert VOICE_COMMANDS["hi off"] == "robot_hand_learned_off"
        assert friendly_voice("hi off") == "HI off"

    def test_a_hud_is_sent_to_a_corner_by_name(self):
        assert VOICE_COMMANDS["main hud top right"] == "main_hud_upper_right"
        assert VOICE_COMMANDS["move main hud top right"] == "main_hud_upper_right"
        assert VOICE_COMMANDS["portrait hud lower left"] == "portrait_hud_lower_left"
        assert VOICE_COMMANDS["landscape hud upper left"] == "landscape_hud_upper_left"
        assert VOICE_COMMANDS["satellites hud top left"] == "satellites_hud_upper_left"
        assert VOICE_COMMANDS["hud lower right"] == "active_hud_lower_right"
        assert VOICE_COMMANDS["move hud lower right"] == "active_hud_lower_right"
        assert VOICE_COMMANDS["main hud bottom left"] == "main_hud_lower_left"
        assert VOICE_COMMANDS["move hud bottom right"] == "active_hud_lower_right"
        assert VOICE_COMMANDS["satellites hud bottom right"] == "satellites_hud_lower_right"

    def test_no_phrase_uses_a_word_vosk_cannot_hear(self):
        oov_words = {*_SAID_INSTEAD_OF_A_WORD_VOSK_LACKS, "unretract"}
        heard_as_reset = {"recents"}
        for phrase in VOICE_COMMANDS:
            offenders = (oov_words | heard_as_reset) & set(phrase.split())
            assert not offenders, f"{phrase!r} uses out-of-vocabulary {sorted(offenders)}"

    def test_every_word_vosk_lacks_is_said_in_words_it_has(self):
        unsayable = {word: said for word, said in _SAID_INSTEAD_OF_A_WORD_VOSK_LACKS.items()
                     if not any(said in phrase for phrase in VOICE_COMMANDS)}

        assert unsayable == {}

    def test_the_holds_stop_the_device_without_pausing_the_app(self):
        assert VOICE_COMMANDS["park"] == "robot_hand_park"
        assert VOICE_COMMANDS["park it"] == "robot_hand_park"
        assert VOICE_COMMANDS["stop"] == "relief_omnipause"
        assert VOICE_COMMANDS["un park"] == "robot_hand_release"
        for phrase in ("o s r two resume", "oh es are two resume"):
            assert phrase not in VOICE_COMMANDS, phrase

    def test_retract_is_no_word_now_the_switch_has_no_retract(self):
        assert "retract" not in VOICE_COMMANDS
        assert "un retract" not in VOICE_COMMANDS

    def test_letting_go_of_the_device_is_the_huds_switch_and_no_phrase(self):
        for phrase in ("o s r two off", "oh es are two off"):
            assert phrase not in VOICE_COMMANDS, phrase
        assert "osr2_control_off" not in VOICE_COMMANDS.values()

    def test_osr2_control_is_the_switch_by_the_name_its_label_gives_it(self):
        for phrase in ("o s r two control off", "oh es are two control off"):
            assert VOICE_COMMANDS[phrase] == "robot_hand_park", phrase
            assert friendly_voice(phrase) == "OSR2 control off"
        for phrase in ("o s r two control on", "oh es are two control on"):
            assert VOICE_COMMANDS[phrase] == "robot_hand_release", phrase
            assert friendly_voice(phrase) == "OSR2 control on"

    def test_the_release_is_spelled_the_way_vosk_can_hear_it(self):
        for joined in ("unpark", "osr2", "o s r 2"):
            assert joined not in VOICE_COMMANDS
        assert friendly_voice("un park") == "unpark"
        assert VOICE_COMMANDS["resume"] == "play"

    def test_unmute_is_heard_as_two_words_and_shown_as_one(self):
        assert VOICE_COMMANDS["un mute"] == "active_unmute"
        assert friendly_voice("un mute") == "unmute"

    def test_reference_popup_phrases_toggle_and_close_help(self):
        for phrase in ("help", "reference", "hot keys", "voice commands"):
            assert VOICE_COMMANDS[phrase] == "help_reference"
            assert VOICE_COMMANDS[f"close {phrase}"] == "help_reference_close"
        assert friendly_voice("close hot keys") == "close hotkeys"

    def test_every_go_now_phrase_has_a_twin_that_says_genau(self):
        go_now = {phrase: command for phrase, command in VOICE_COMMANDS.items() if "go now" in phrase}
        twins = {phrase.replace("go now", "genau"): command.replace("go_now", "genau")
                 for phrase, command in go_now.items()}

        assert len(go_now) == 2
        assert {phrase: VOICE_COMMANDS.get(phrase) for phrase in twins} == twins

    def test_a_hosted_apps_phrase_is_aimed_like_the_grid(self):
        heard = build_voice_commands(filter_commands={}, clip_jump_phrases=(), flick_flip_phrases=(),
                                     origenerator_phrases=("experiments", "fix hands",
                                                           "play slideshow", "favorites"))
        for phrase in ("experiments", "fix hands", "play slideshow", "favorites"):
            said = phrase.replace(" ", "_")
            assert heard[phrase] == f"active_say_{said}"
            assert heard[f"portrait {phrase}"] == f"portrait_say_{said}"
            assert heard[f"{phrase} landscape"] == f"landscape_say_{said}"
            assert heard[f"satellites {phrase}"] == f"satellites_say_{said}"
            assert heard[f"{phrase} satellites"] == f"satellites_say_{said}"
        assert VOICE_COMMANDS["favorites"] == "active_say_favorites"

    def test_the_enhancement_level_is_said_as_the_shapes_are_of_any_funestra(self):
        grid = {"enhanced only": "enhancement_enhanced", "original only": "enhancement_original",
                "enhanced and original": "enhancement_both", "original and enhanced": "enhancement_both"}
        for phrase, act in grid.items():
            assert VOICE_COMMANDS[phrase] == f"active_{act}"
            assert VOICE_COMMANDS[f"main {phrase}"] == VOICE_COMMANDS[f"{phrase} main"] == f"main_{act}"
            assert VOICE_COMMANDS[f"portrait {phrase}"] == f"portrait_{act}"
            assert VOICE_COMMANDS[f"{phrase} satellites"] == f"satellites_{act}"
        heard = build_voice_commands(filter_commands={}, clip_jump_phrases=(), flick_flip_phrases=(),
                                     origenerator_phrases=("enhanced only",))
        assert heard["enhanced only"] == "active_enhancement_enhanced"
        assert "filter enhanced" not in VOICE_COMMANDS

    def test_genau_alone_stays_the_mode_and_animates_a_picture_only_with_a_side_named(self):
        heard = build_voice_commands(filter_commands={}, clip_jump_phrases=(), flick_flip_phrases=(),
                                     origenerator_phrases=("genau", "go now"))
        assert heard["genau"] == heard["go now"] == "genau_activate"
        assert heard["satellites genau"] == "satellites_say_genau"
        assert heard["go now portrait"] == "portrait_say_go_now"
        assert not {"active_say_genau", "active_say_go_now"} & set(heard.values())

    def test_aura_generator_shows_origenerator_core_in_the_browsers_place(self):
        assert VOICE_COMMANDS["aura generator"] == "origenerator_activate"
        assert VOICE_COMMANDS["aura generator mode"] == "origenerator_activate"
        for retired in ("portrait aura generator mode", "landscape aura generator mode"):
            assert retired not in VOICE_COMMANDS, retired

    def test_aura_generator_reads_as_origenerator(self):
        assert friendly_voice("aura generator mode") == "origenerator mode"

    @pytest.mark.parametrize("said", ["rfb", "r f b", "are eff bee"])
    def test_rfb_reads_as_its_letters_however_it_was_heard(self, said):
        assert friendly_voice(said) == "RFB"

    def test_dead_genau_phrases_removed(self):
        for phrase in ("enable genau", "disable genau"):
            assert phrase not in VOICE_COMMANDS

    def test_kino_mode_is_said_of_the_main_funestra(self):
        assert VOICE_COMMANDS["main kino mode"] == "main_kino_activate"
        assert VOICE_COMMANDS["kino mode"] == "kino_activate"
        assert VOICE_COMMANDS["kino"] == "kino_activate"
        for retired in ("now now", "now mode", "hybrid", "hybrid mode", "player mode", "v l c",
                        "video mode", "main video mode", "satellite video mode",
                        "satellites video mode"):
            assert retired not in VOICE_COMMANDS, retired

    def test_main_funestra_version_is_spoken_like_every_other_cycle_axis(self):
        for phrase in ("version", "cycle version", "next version", "change version"):
            assert VOICE_COMMANDS[phrase] == "main_funestra_cycle_version"

    def test_a_satellite_version_is_said_of_that_satellite_and_never_bare(self):
        for funestra in SATELLITE_WORDS:
            assert VOICE_COMMANDS[f"{funestra} version"] == f"{funestra}_cycle_version"
            assert VOICE_COMMANDS[f"version {funestra}"] == f"{funestra}_cycle_version"
        assert VOICE_COMMANDS["version"] == "main_funestra_cycle_version"

    def test_the_main_funestras_version_is_said_of_it_as_a_satellites_is(self):
        assert VOICE_COMMANDS["main version"] == VOICE_COMMANDS["version main"] == (
            "main_funestra_cycle_version")

    def test_the_main_funestras_snippet_is_dropped_by_naming_the_snippet(self):
        for words in ("end snippet", "no snippet", "snippet off"):
            assert VOICE_COMMANDS[words] == "main_funestra_snippet_cancel"

    def test_no_loop_word_reaches_the_main_funestras_snippet(self):
        for words in ("end loop", "no loop", "loop off"):
            assert f"main {words}" not in VOICE_COMMANDS
            assert f"{words} main" not in VOICE_COMMANDS
        assert "loop" not in VOICE_COMMANDS

    def test_the_lengths_are_said_like_the_shapes_as_what_is_included(self):
        assert VOICE_COMMANDS["clips only"] == "main_funestra_length_clips"
        assert VOICE_COMMANDS["full only"] == "main_funestra_length_full"
        assert VOICE_COMMANDS["clips and full"] == "main_funestra_length_mixed"
        assert VOICE_COMMANDS["full and clips"] == "main_funestra_length_mixed"
        for retired in ("clips", "full", "mixed", "shorts", "full length"):
            assert retired not in VOICE_COMMANDS, retired

    def test_main_funestra_orientation_phrases_say_it_the_way_the_shapes_are_said(self):
        assert VOICE_COMMANDS["portrait only"] == "main_orientation_portrait"
        assert VOICE_COMMANDS["landscape only"] == "main_orientation_landscape"
        assert VOICE_COMMANDS["portrait and landscape"] == "main_orientation_both"
        assert VOICE_COMMANDS["landscape and portrait"] == "main_orientation_both"

    def test_end_compilation_leaves_without_naming_a_length(self):
        assert VOICE_COMMANDS["end compilation"] == "main_funestra_end_compilation"

    def test_main_reset_returns_the_playlist_to_the_default_browse(self):
        for phrase in ("main reset", "reset main"):
            assert VOICE_COMMANDS[phrase] == "main_reset"

    def test_satellite_grid_supports_both_orders(self):
        actions = {
            "lock": "lock_on",
            "unlock": "lock_off",
            "next": "next",
            "previous": "prev",
            "trash": "trash",
            "action": "cycle_action",
            "scene": "cycle_action",
            "seed": "cycle_seed",
        }
        for word, act in actions.items():
            assert VOICE_COMMANDS[word] == f"active_{act}"
            for satellite in SATELLITE_WORDS:
                target = f"{satellite}_{act}"
                assert VOICE_COMMANDS[f"{satellite} {word}"] == target
                assert VOICE_COMMANDS[f"{word} {satellite}"] == target

    def test_f_mode_is_sayable_per_funestra_in_either_order(self):
        for word, act in (("f mode", "fmode"),
                          ("f mode on", "fmode_on"),
                          ("f mode off", "fmode_off")):
            for satellite in FUNESTRAS:
                assert VOICE_COMMANDS[f"{satellite} {word}"] == f"{satellite}_{act}"
                assert VOICE_COMMANDS[f"{word} {satellite}"] == f"{satellite}_{act}"

    def test_bare_f_mode_reaches_the_active_funestra(self):
        assert VOICE_COMMANDS["f mode"] == "active_fmode"
        assert VOICE_COMMANDS["f mode on"] == "active_fmode_on"
        assert VOICE_COMMANDS["f mode off"] == "active_fmode_off"

    def test_all_f_mode_is_the_whole_app_in_either_order(self):
        for word, act in (("f mode", "fmode_toggle"),
                          ("f mode on", "fmode_on"),
                          ("f mode off", "fmode_off")):
            assert VOICE_COMMANDS[f"all {word}"] == act
            assert VOICE_COMMANDS[f"{word} all"] == act

    def test_cycle_verb_phrases_map_to_the_active_cycle(self):
        for phrase in ("cycle action", "next action", "change action",
                       "cycle scene", "next scene", "change scene"):
            assert VOICE_COMMANDS[phrase] == "active_cycle_action"
        for phrase in ("cycle seed", "next seed", "change seed"):
            assert VOICE_COMMANDS[phrase] == "active_cycle_seed"

    def test_loop_family_phrases(self):
        for phrase in ("loop actions", "loop action", "action loop", "loop scenes", "loop scene"):
            assert VOICE_COMMANDS[phrase] == "active_action_loop"
        for phrase in ("loop seeds", "loop seed", "seed loop"):
            assert VOICE_COMMANDS[phrase] == "active_seed_loop"
        for phrase in ("no loop", "loop off"):
            assert VOICE_COMMANDS[phrase] == "active_no_loop"
        assert VOICE_COMMANDS["portrait loop actions"] == "portrait_action_loop"
        assert VOICE_COMMANDS["loop actions portrait"] == "portrait_action_loop"
        assert VOICE_COMMANDS["satellites no loop"] == "satellites_no_loop"
        assert VOICE_COMMANDS["more seeds"] == "active_more_seeds"
        assert VOICE_COMMANDS["portrait more seeds"] == "portrait_more_seeds"
        assert VOICE_COMMANDS["widen net"] == "active_more_seeds"
        assert VOICE_COMMANDS["widen the net"] == "active_more_seeds"
        assert VOICE_COMMANDS["portrait widen the net"] == "portrait_more_seeds"

    def test_grid_lock_scopes_are_aliases_of_existing_commands(self):
        assert VOICE_COMMANDS["lock seed"] == "active_action_loop"
        assert VOICE_COMMANDS["lock type"] == "active_seed_loop"
        assert VOICE_COMMANDS["portrait lock seed"] == "portrait_action_loop"
        assert VOICE_COMMANDS["lock type landscape"] == "landscape_seed_loop"

    def test_all_means_every_funestra_and_never_everything_holding_a_video(self):
        for phrase in ("lock all", "loop all", "same all"):
            assert phrase not in VOICE_COMMANDS
            for satellite in SATELLITE_WORDS:
                assert f"{satellite} {phrase}" not in VOICE_COMMANDS
                assert f"{phrase} {satellite}" not in VOICE_COMMANDS
        assert VOICE_COMMANDS["lock"] == "active_lock_on"
        assert VOICE_COMMANDS["reset"] == "active_reset"
        assert VOICE_COMMANDS["all f mode"] == "fmode_toggle"

    def test_main_nav_phrases_both_orders(self):
        assert VOICE_COMMANDS["main next"] == "main_next"
        assert VOICE_COMMANDS["next main"] == "main_next"
        assert VOICE_COMMANDS["main previous"] == "main_prev"
        assert VOICE_COMMANDS["previous main"] == "main_prev"

    def test_main_lock_phrases_both_orders(self):
        assert VOICE_COMMANDS["main lock"] == "main_lock_on"
        assert VOICE_COMMANDS["lock main"] == "main_lock_on"
        assert VOICE_COMMANDS["main unlock"] == "main_lock_off"
        assert VOICE_COMMANDS["unlock main"] == "main_lock_off"

    def test_primary_is_a_monitors_name_and_never_a_funestras(self):
        spoken = {phrase for phrase in VOICE_COMMANDS if "primary" in phrase}

        assert spoken == set()

    def test_the_flick_is_stepped_by_its_own_name_and_no_longer_by_genaus(self):
        for base in ("genau", "go now"):
            for step in ("next", "previous"):
                assert f"{base} {step}" not in VOICE_COMMANDS, f"{base} {step}"
                assert f"{step} {base}" not in VOICE_COMMANDS, f"{step} {base}"
        flick = genau_flick_noun().one
        assert VOICE_COMMANDS[f"next {flick}"] == "genau_next_flick"
        assert VOICE_COMMANDS[f"previous {flick}"] == "genau_prev_flick"

    def test_next_video_and_previous_video_say_next_and_previous(self):
        assert VOICE_COMMANDS["next video"] == VOICE_COMMANDS["next"] == "active_next"
        assert VOICE_COMMANDS["previous video"] == VOICE_COMMANDS["previous"] == "active_prev"
        for retired in ("kino next", "next kino", "kino previous", "previous kino",
                        "video next", "video previous"):
            assert retired not in VOICE_COMMANDS, retired

    def test_trash_lock_and_unlock_take_video_after_them_the_way_next_does(self):
        assert VOICE_COMMANDS["trash video"] == VOICE_COMMANDS["trash"] == "active_trash"
        assert VOICE_COMMANDS["lock video"] == VOICE_COMMANDS["lock"] == "active_lock_on"
        assert VOICE_COMMANDS["unlock video"] == VOICE_COMMANDS["unlock"] == "active_lock_off"

    def test_holding_a_genau_flick_is_the_main_lock_said_with_the_flick_named(self):
        said = build_voice_commands(genau_flick_words=Noun(one="pip", many="pips"))

        assert (said["lock pip"], said["unlock pip"]) == ("main_lock_on", "main_lock_off")

    def test_satellites_is_the_word_for_the_pair_and_both_is_no_longer_heard(self):
        assert VOICE_COMMANDS["satellites next"] == VOICE_COMMANDS["next satellites"] == "satellites_next"
        assert VOICE_COMMANDS["satellites f mode"] == "satellites_fmode"
        assert VOICE_COMMANDS["satellites version"] == "satellites_cycle_version"
        assert VOICE_COMMANDS["satellites hud top left"] == "satellites_hud_upper_left"
        assert VOICE_COMMANDS["minimize satellites hud"] == "satellites_hud_minimize"
        assert VOICE_COMMANDS["satellites half speed"] == "satellites_speed_50"
        assert VOICE_COMMANDS["satellites quiet"] == "satellites_volume_down"
        assert VOICE_COMMANDS["satellites end loop"] == "satellites_no_loop"
        assert VOICE_COMMANDS["satellites loop actions"] == "satellites_action_loop"
        assert not [phrase for phrase in VOICE_COMMANDS if "both" in phrase.split()]
        assert not [command for command in VOICE_COMMANDS.values() if command.startswith("both_")]

    def test_contains_numeric_amp_phrases(self):
        assert VOICE_COMMANDS["amp fifty"] == "robot_hand_amp_50"
        assert VOICE_COMMANDS["amp zero"] == "robot_hand_amp_0"
        assert VOICE_COMMANDS["amp one hundred"] == "robot_hand_amp_100"

    def test_contains_numeric_center_phrases(self):
        assert VOICE_COMMANDS["center eighty"] == "robot_hand_center_80"

    def test_contains_numeric_speed_phrases(self):
        assert VOICE_COMMANDS["speed thirty"] == "robot_hand_speed_30"

    def test_min_max_extreme_phrases(self):
        assert VOICE_COMMANDS["min amp"] == "robot_hand_amp_0"
        assert VOICE_COMMANDS["max amp"] == "robot_hand_amp_100"
        assert VOICE_COMMANDS["min center"] == "robot_hand_center_0"
        assert VOICE_COMMANDS["max center"] == "robot_hand_center_100"

    def test_a_named_multiplier_is_a_rate_in_percent(self):
        assert VOICE_COMMANDS["half speed"] == "active_speed_50"
        assert VOICE_COMMANDS["normal speed"] == "active_speed_100"
        assert VOICE_COMMANDS["one and a half speed"] == "active_speed_150"
        assert VOICE_COMMANDS["double speed"] == "active_speed_200"

    def test_spoken_speed_ex_phrases_loading_screen_every_stop(self):
        assert VOICE_COMMANDS["speed point two five ex"] == "active_speed_25"
        assert VOICE_COMMANDS["speed one ex"] == "active_speed_100"
        assert VOICE_COMMANDS["speed one point two five ex"] == "active_speed_125"
        assert VOICE_COMMANDS["speed one point seven five ex"] == "active_speed_175"
        assert VOICE_COMMANDS["speed two ex"] == "active_speed_200"

    def test_reset_speed_snaps_to_normal(self):
        assert VOICE_COMMANDS["reset speed"] == "active_speed_100"

    def test_a_playback_speed_phrase_reaches_the_funestra_last_addressed_or_the_one_named(self):
        grid = {
            "playback speed up": "speed_up",
            "playback speed down": "speed_down",
            "playback slow down": "speed_down",
            "min speed": "speed_min",
            "max speed": "speed_max",
            "half speed": "speed_50",
            "speed one point five ex": "speed_150",
            "reset speed": "speed_100",
        }
        for phrase, act in grid.items():
            assert VOICE_COMMANDS[phrase] == f"active_{act}"
            for satellite in SATELLITE_WORDS:
                assert VOICE_COMMANDS[f"{satellite} {phrase}"] == f"{satellite}_{act}"
                assert VOICE_COMMANDS[f"{phrase} {satellite}"] == f"{satellite}_{act}"
            assert VOICE_COMMANDS[f"main {phrase}"] == f"main_funestra_{act}"
            assert VOICE_COMMANDS[f"{phrase} main"] == f"main_funestra_{act}"


def test_group_commands_join_the_order_agnostic_grid():
    for words, act in {
        ("action loop", "loop action"): "action_loop",
        ("seed loop", "loop seed"): "seed_loop",
        ("lock action", "action lock", "filter"): "lock_action",
    }.items():
        for word in words:
            assert VOICE_COMMANDS[word] == f"active_{act}"
            for satellite in SATELLITE_WORDS:
                assert VOICE_COMMANDS[f"{satellite} {word}"] == f"{satellite}_{act}"
                assert VOICE_COMMANDS[f"{word} {satellite}"] == f"{satellite}_{act}"


def test_full_vid_is_a_fallback_beside_the_reliably_heard_full_video():
    assert VOICE_COMMANDS["full vid"] == VOICE_COMMANDS["full video"] == "main_funestra_full_vid"


def test_resume_and_unpause_are_synonyms_for_play():
    assert VOICE_COMMANDS["resume"] == "play"
    assert VOICE_COMMANDS["un pause"] == "play"
    assert "unpause" not in VOICE_COMMANDS


def test_shuffle_joins_the_order_agnostic_satellite_grid():
    assert VOICE_COMMANDS["shuffle"] == "active_shuffle"
    for satellite in SATELLITE_WORDS:
        assert VOICE_COMMANDS[f"{satellite} shuffle"] == f"{satellite}_shuffle"
        assert VOICE_COMMANDS[f"shuffle {satellite}"] == f"{satellite}_shuffle"


def test_skip_and_back_join_the_grid_for_every_funestra():
    for word, act in (("skip", "nudge_next"), ("back", "nudge_prev")):
        assert VOICE_COMMANDS[word] == f"active_{act}"
        for satellite in FUNESTRAS:
            assert VOICE_COMMANDS[f"{satellite} {word}"] == f"{satellite}_{act}"
            assert VOICE_COMMANDS[f"{word} {satellite}"] == f"{satellite}_{act}"


def test_reset_joins_the_order_agnostic_satellite_grid():
    assert VOICE_COMMANDS["reset"] == "active_reset"
    for satellite in SATELLITE_WORDS:
        assert VOICE_COMMANDS[f"{satellite} reset"] == f"{satellite}_reset"
        assert VOICE_COMMANDS[f"reset {satellite}"] == f"{satellite}_reset"


def test_reset_all_reaches_every_funestra_in_either_order():
    for phrase in ("reset all", "all reset"):
        assert VOICE_COMMANDS[phrase] == "all_reset"


def test_wrong_action_joins_the_order_agnostic_satellite_grid():
    assert VOICE_COMMANDS["wrong action"] == "active_wrong_action"
    for satellite in SATELLITE_WORDS:
        assert VOICE_COMMANDS[f"{satellite} wrong action"] == f"{satellite}_wrong_action"
        assert VOICE_COMMANDS[f"wrong action {satellite}"] == f"{satellite}_wrong_action"
    assert VOICE_COMMANDS["action"] == "active_cycle_action"


def test_group_commands_do_not_shadow_the_single_word_actions():
    assert VOICE_COMMANDS["lock"] == "active_lock_on"
    assert VOICE_COMMANDS["action"] == "active_cycle_action"
    assert VOICE_COMMANDS["seed"] == "active_cycle_seed"
    assert VOICE_COMMANDS["snippet"] == "main_funestra_record_up"
    assert VOICE_COMMANDS["no filter"] == "active_no_filter"
    assert VOICE_COMMANDS["filter off"] == "active_no_filter"


def test_the_filter_phrases_a_session_is_handed_are_heard_in_every_form():
    acts = {"alpha": ("alpha", "al fa"), "beta gamma": ("beta gamma",)}
    commands = build_voice_commands(filter_commands=filter_voice_commands(acts))
    for query, forms in acts.items():
        for form in forms:
            assert commands[f"portrait {form}"] == set_command("portrait", query)
            assert commands[form] == set_command("satellites", query)


def test_a_session_handed_no_filter_phrases_hears_none():
    assert not any(parse_command(command).action == FILTER for command in VOICE_COMMANDS.values())


def test_clearing_a_filter_scopes_like_every_other_satellite_action():
    for phrase in ("no filter", "filter off", "clear filter", "show everything"):
        assert VOICE_COMMANDS[phrase] == "active_no_filter"
        assert VOICE_COMMANDS[f"portrait {phrase}"] == "portrait_no_filter"
        assert VOICE_COMMANDS[f"{phrase} landscape"] == "landscape_no_filter"
        assert VOICE_COMMANDS[f"satellites {phrase}"] == "satellites_no_filter"
    assert "clear portrait" not in VOICE_COMMANDS
    assert "clear landscape" not in VOICE_COMMANDS


def test_filter_phrases_do_not_shadow_other_commands():
    told = filter_voice_commands({"alpha": ("alpha", "al fa")})
    commands = build_voice_commands(filter_commands=told)
    for phrase, command in told.items():
        assert commands[phrase] == command
        assert parse_command(command).action == FILTER


class TestBuildVoiceCommands:
    def test_builds_from_explicit_inputs_without_touching_the_module_global(self):
        built = build_voice_commands(
            filter_commands={"fabricated act": "filter_satellites_fabricated_act"},
            clip_jump_phrases=("fabricated jump phrase",),
        )
        assert built["fabricated jump phrase"] == "main_funestra_clip_jump"
        assert built["fabricated act"] == "filter_satellites_fabricated_act"
        assert "fabricated jump phrase" not in VOICE_COMMANDS
        assert built["quit"] == "quit"

    def test_every_clip_jump_phrase_is_the_library_overlays_and_none_is_written_here(self):
        built = build_voice_commands(filter_commands={}, clip_jump_phrases=(), flick_flip_phrases=())
        assert "main_funestra_clip_jump" not in built.values()

    def test_the_built_vocabulary_is_read_only(self):
        built = build_voice_commands(filter_commands={}, clip_jump_phrases=())
        with pytest.raises(TypeError):
            built["fabricated"] = "quit"

    def test_a_phrase_the_hosted_app_answers_is_heard_said_to_either_satellite(self):
        built = build_voice_commands(filter_commands={}, clip_jump_phrases=(),
                                     origenerator_phrases=frozenset({"fabricated order"}))

        assert (built["portrait fabricated order"], built["landscape fabricated order"]) == (
            "portrait_say_fabricated_order", "landscape_say_fabricated_order")

    def test_a_hosted_phrase_the_session_already_says_to_a_satellite_stays_the_sessions(self):
        built = build_voice_commands(filter_commands={"portrait alpha": "filter_portrait_alpha"},
                                     clip_jump_phrases=(),
                                     origenerator_phrases=frozenset({"next", "alpha"}))

        assert (built["portrait next"], built["portrait alpha"]) == (
            "portrait_next", "filter_portrait_alpha")

    def test_an_action_name_that_is_already_a_spoken_command_leaves_the_command_as_it_was(self, caplog):
        built = build_voice_commands(
            filter_commands={"pause": "filter_satellites_pause", "alpha": "filter_satellites_alpha"},
            clip_jump_phrases=(),
        )

        assert (built["pause"], built["alpha"]) == (VOICE_COMMANDS["pause"], "filter_satellites_alpha")
        assert "pause" in caplog.text


_FUNESTRA_WORDS = frozenset(("portrait", "landscape", "main", "all", "satellite", "satellites"))


def test_a_phrase_naming_a_funestra_takes_the_name_first_or_last():
    vocabulary = build_voice_commands(
        filter_commands=filter_voice_commands({"alpha": ("alpha",), "beta gamma": ("beta gamma",)}),
        clip_jump_phrases=(), flick_flip_phrases=(), genau_flick_words=Noun("flick", "flicks"))
    missing = {}
    for phrase, command in vocabulary.items():
        words = phrase.split()
        named = [word for word in words if word in _FUNESTRA_WORDS]
        rest = [word for word in words if word not in _FUNESTRA_WORDS]
        if len(named) != 1 or not rest:
            continue
        for twin in (" ".join(named + rest), " ".join(rest + named)):
            if vocabulary.get(twin) != command:
                missing[twin] = command
    assert missing == {}


def test_the_crown_is_given_by_naming_the_funestra_after_the_word_crown():
    assert (VOICE_COMMANDS["crown portrait"], VOICE_COMMANDS["crown main"]) == (
        "portrait_crown", "main_crown")


_WHISPER_READS_AS_NEITHER = "un minimize"


class TestMinimizingAHudBySpeech:
    @pytest.mark.parametrize("said", ["{verb} {hud}", "{hud} {verb}"])
    @pytest.mark.parametrize(("verb", "act"), [
        ("minimize", "hud_minimize"), ("collapse", "hud_minimize"),
        ("restore", "hud_restore"), ("maximize", "hud_restore"), ("expand", "hud_restore"),
    ])
    def test_the_verb_goes_before_or_after_the_hud_it_names(self, said, verb, act):
        assert VOICE_COMMANDS.get(said.format(verb=verb, hud="hud")) == f"active_{act}"
        for funestra in FUNESTRAS:
            assert (VOICE_COMMANDS.get(said.format(verb=verb, hud=f"{funestra} hud"))
                    == f"{funestra}_{act}")

    def test_no_phrase_says_what_whisper_cannot_tell_from_minimize(self):
        assert [phrase for phrase in VOICE_COMMANDS if _WHISPER_READS_AS_NEITHER in phrase] == []


def test_saving_a_snippet_is_said_as_a_snippet_and_never_as_a_loop():
    assert VOICE_COMMANDS["save snippet"] == "genaumacher_save"
    assert "save loop" not in VOICE_COMMANDS
