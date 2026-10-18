"""The spoken vocabulary and its wire format.

These tests are about :mod:`fun_time.voice_commands` alone — the generated
phrase grids and the command-file line format — and import it directly, not
through :mod:`fun_time.voice_control`, whose vosk/sounddevice runtime is
exactly the coupling the module was split out to prevent.
"""
from __future__ import annotations

import pytest

from fun_time.content import Noun
from fun_time.filter_vocab import (
    decode_filter_command,
    filter_voice_commands,
    load_acts,
    set_command,
)
from fun_time.voice_commands import (
    VOICE_COMMANDS,
    CommandLine,
    build_voice_commands,
    format_spoken_command,
    friendly_voice,
    parse_command_line,
    said_to_a_side,
    say_command,
)


def test_the_words_said_to_a_side_are_read_back_off_the_command_they_were_written_as():
    assert said_to_a_side(say_command("landscape", "fix alpha hands")) == (
        "landscape", "fix alpha hands")
    assert said_to_a_side("landscape_next") is None


class TestCommandLineFormat:
    def test_round_trips_the_utterance_start_and_what_was_said(self):
        line = format_spoken_command("portrait_lock_on", spoken_at=1234.5, said="lock portrait")
        assert parse_command_line(line) == CommandLine("portrait_lock_on", 1234.5, "lock portrait")

    def test_an_unstamped_line_is_a_bare_command(self):
        """Hotkeys and dashboard presses are instantaneous — no back-dating."""
        assert parse_command_line("portrait_next") == CommandLine("portrait_next")

    def test_a_command_ending_in_an_unparseable_stamp_stays_whole(self):
        assert parse_command_line("filter_both_come @ shot") == CommandLine("filter_both_come @ shot")


class TestTheSpokenCrossing:
    """"enter VR" / "exit VR" — the whole way into the headset, since there is
    no key for it and no pin left to click (docs/entering-vr.md)."""

    @pytest.mark.parametrize(
        ("phrase", "command"),
        [
            ("enter vr", "enter_vr"),
            ("enter v r", "enter_vr"),
            ("exit vr", "exit_vr"),
            ("exit v r", "exit_vr"),
        ],
    )
    def test_both_spellings_of_the_name_answer(self, phrase, command):
        """The model has "vr" as a word AND has the two letters, and which of
        them it hears the sound as is not something to bet a one-way door on."""
        assert VOICE_COMMANDS[phrase] == command

    def test_no_phrase_a_crossing_starts_with_is_a_command_of_its_own(self):
        """The rule the first spoken "exit VR" bought: both it and a bare "exit"
        were whole phrases in the grammar, the shorter scored better, and the
        session ended instead of leaving the headset.  "enter VR" was never at
        risk because no bare "enter" was there to win."""
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
    def test_the_reference_and_the_notices_show_one_spelling(self, phrase, shown):
        assert friendly_voice(phrase) == shown


class TestTheVrReset:
    @pytest.mark.parametrize("phrase", ["vr reset", "v r reset"])
    def test_both_spellings_put_the_funestras_back_and_read_as_vr_reset(self, phrase):
        assert VOICE_COMMANDS[phrase] == "vr_reset"
        assert friendly_voice(phrase) == "VR reset"


class TestVoiceCommands:
    def test_quit_is_the_word_that_quits(self):
        assert VOICE_COMMANDS["quit"] == "quit"
        assert "exit" not in VOICE_COMMANDS

    def test_contains_all_static_phrases(self):
        static_phrases = {
            "quit": "quit",
            "pause": "pause",
            "play": "play",
            "go now": "genau_activate",
            "go now mode": "genau_activate",
            "kino mode": "kino_activate",
            "main kino mode": "main_kino_activate",
            "satellite kino mode": "satellites_kino_activate",
            "satellites kino mode": "satellites_kino_activate",
            "aura generator mode": "satellites_origenerator_activate",
            "start broker": "broker_start",
            "stop broker": "broker_stop",
            "next main": "main_next",
            "previous main": "main_prev",
            "skip": "active_nudge_next",
            "back": "active_nudge_prev",
            "record": "main_player_record_down",
            "loop": "main_player_record_up",
            # "end loop" is side-agnostic — it reaches the Main Funestra's own loop through the
            # active-side resolution, not by naming the Funestra here.
            "end loop": "active_no_loop",
            # The bare axis word is the literal one; "cycle / next / change
            # version" are generated with the other cycle axes.
            "version": "main_player_cycle_version",
            "clips": "main_player_length_clips",
            "full": "main_player_length_full",
            "browse": "browse_library",
            "clip": "genaumacher_save",
            "save clip": "genaumacher_save",
            # Engine-agnostic: routed to whichever holds the OSR2, not to Genau
            # by name (the console's own marks are the by-name pair).
            "slow down": "speed_down",
            "speed down": "speed_down",
            "speed up": "speed_up",
            # …and naming the playback moves the video of the Funestra last addressed.
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
            "offset": "quarter_button",
            "voice off": "voice_off",
            "mic off": "voice_off",
        }
        for phrase, cmd in static_phrases.items():
            assert VOICE_COMMANDS[phrase] == cmd

    def test_genaus_flicks_are_said_in_the_overlays_word(self):
        said = build_voice_commands(genau_flick_words=Noun(one="pip", many="pips"))

        assert {phrase: said[phrase] for phrase in ("previous pip", "next pip", "weird pip")} == {
            "previous pip": "genau_prev_flick",
            "next pip": "genau_next_flick",
            "weird pip": "genau_weird_flick",
        }
        assert [phrase for phrase, command in said.items()
                if command.startswith("genau_") and "clip" in phrase.split()] == []

    def test_marking_a_flick_weird_names_it_where_bare_weird_means_the_active_satellite(self):
        said = build_voice_commands(genau_flick_words=Noun(one="pip", many="pips"))

        assert (said["weird pip"], said["weird"]) == ("genau_weird_flick", "active_trash")

    def test_the_overlay_says_the_words_that_flip_genaus_flick(self):
        assert build_voice_commands(flick_flip_phrases=("flip alpha",))["flip alpha"] == "genau_flip_ends"

    def test_holding_a_genau_flick_is_the_main_lock_and_nothing_of_its_own(self):
        """It was a phrase and a padlock beside auto advance's arming; the two
        could disagree, and the console carried a second lock next to the Main Funestra's."""
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

    def test_no_spoken_clip_interval_is_zero_seconds(self):
        """A zero-second interval would step the clip every frame."""
        assert not any(cmd == "genau_flick_seconds_0" for cmd in VOICE_COMMANDS.values())

    def test_audio_phrases_mute_and_step_the_volume(self):
        """Both words of each pair mean the same thing, so a speaker never has to
        pick between "quiet" and "quieter"."""
        assert VOICE_COMMANDS["mute"] == "audio_mute"
        assert VOICE_COMMANDS["quiet"] == "audio_volume_down"
        assert VOICE_COMMANDS["quieter"] == "audio_volume_down"
        assert VOICE_COMMANDS["loud"] == "audio_volume_up"
        assert VOICE_COMMANDS["louder"] == "audio_volume_up"

    def test_no_phrase_uses_a_word_vosk_cannot_hear(self):
        """A phrase built from a word outside the model's lexicon can never be
        recognized — the command is unreachable, and silently so.  Every one of
        these has a sound-alike the recognizer listens for instead."""
        # "upscales" is what the desk calls the pictures a hosted Origenerator
        # show can narrow to; the app hears it on its own mic, but this model
        # has no such token, so the relayed phrase is "enhanced only" (and the
        # way back out is the room's own "clear filter", routed to the show).
        oov_words = {"origenerator", "upscales", "unpause", "unpark", "unretract", "funscript",
                     "omnipause", "omniplay", "osr2", "2d"}
        for phrase in VOICE_COMMANDS:
            offenders = oov_words & set(phrase.split())
            assert not offenders, f"{phrase!r} uses out-of-vocabulary {sorted(offenders)}"

    def test_the_holds_stop_the_device_without_pausing_the_room(self):
        """"stop" beside them is relief OmniPause — it freezes everything.  These
        are the narrow gesture: the switch off parks the device and the room
        plays on, and the switch on drives it again.  Relief keeps "stop" (and
        Shift+Esc)."""
        assert VOICE_COMMANDS["park"] == "robot_hand_park"
        assert VOICE_COMMANDS["park it"] == "robot_hand_park"
        assert VOICE_COMMANDS["stop"] == "relief_omnipause"
        for phrase in ("un park", "o s r two resume", "oh es are two resume"):
            assert VOICE_COMMANDS[phrase] == "robot_hand_release", phrase

    def test_retract_is_no_word_now_the_switch_has_no_retract(self):
        assert "retract" not in VOICE_COMMANDS
        assert "un retract" not in VOICE_COMMANDS

    def test_osr2_off_is_the_switch_off(self):
        """Off parks the device, as the console's switch does.  Spelled with the
        letters vosk has, like "OSR2 resume", which is the way back."""
        for phrase in ("o s r two off", "oh es are two off"):
            assert VOICE_COMMANDS[phrase] == "robot_hand_park", phrase
            assert friendly_voice(phrase) == "OSR2 off"

    def test_osr2_control_is_the_switch_by_the_name_its_label_gives_it(self):
        for phrase in ("o s r two control off", "oh es are two control off"):
            assert VOICE_COMMANDS[phrase] == "robot_hand_park", phrase
            assert friendly_voice(phrase) == "OSR2 control off"
        for phrase in ("o s r two control on", "oh es are two control on"):
            assert VOICE_COMMANDS[phrase] == "robot_hand_release", phrase
            assert friendly_voice(phrase) == "OSR2 control on"

    def test_the_release_is_spelled_the_way_vosk_can_hear_it(self):
        """vosk has none of "unpark" or "OSR2" in its lexicon, so a
        phrase built from them could never be recognized.  It does have "un" (as
        "un mute" already relies on) and it has the letters, in both renderings
        the model produces for "oh-ess-arr-two" — so those are the recognizer
        forms, and friendly_voice joins them back up for the reference.

        None of them can collide with bare "resume" (leave OmniPause): the
        grammar matches whole phrases, not prefixes."""
        for joined in ("unpark", "osr2", "o s r 2"):
            assert joined not in VOICE_COMMANDS
        assert friendly_voice("un park") == "unpark"
        assert friendly_voice("o s r two resume") == "OSR2 resume"
        assert friendly_voice("oh es are two resume") == "OSR2 resume"
        assert VOICE_COMMANDS["resume"] == "play"

    def test_unmute_is_heard_as_two_words_and_shown_as_one(self):
        assert VOICE_COMMANDS["un mute"] == "audio_unmute"
        assert friendly_voice("un mute") == "unmute"

    def test_reference_popup_phrases_toggle_and_close_help(self):
        # Several spoken names toggle the hotkeys & voice reference popup; the
        # same names prefixed with "close" only dismiss it.
        for phrase in ("help", "reference", "hot keys", "voice commands"):
            assert VOICE_COMMANDS[phrase] == "help_reference"
            assert VOICE_COMMANDS[f"close {phrase}"] == "help_reference_close"
        assert friendly_voice("close hot keys") == "close hotkeys"

    def test_every_go_now_phrase_has_a_twin_that_says_genau(self):
        go_now = {phrase: command for phrase, command in VOICE_COMMANDS.items() if "go now" in phrase}
        twins = {phrase.replace("go now", "genau"): command.replace("go_now", "genau")
                 for phrase, command in go_now.items()}

        assert len(go_now) == 6
        assert {phrase: VOICE_COMMANDS.get(phrase) for phrase in twins} == twins

    def test_origenerator_mode_puts_both_satellites_in_it_and_is_shown_as_said(self):
        """The spoken twin of each satellite's Origenerator button, said of both:
        what holds the Random Favs Browser's place is the dashboard strip's."""
        assert VOICE_COMMANDS["aura generator mode"] == "satellites_origenerator_activate"
        assert friendly_voice("aura generator mode") == "origenerator mode"

    def test_one_satellites_own_kino_and_origenerator_modes_are_said_of_it(self):
        assert VOICE_COMMANDS["portrait kino mode"] == VOICE_COMMANDS["kino mode portrait"] == (
            "portrait_kino_activate")
        assert (VOICE_COMMANDS["landscape aura generator mode"]
                == VOICE_COMMANDS["aura generator mode landscape"]
                == "landscape_origenerator_activate")
        assert friendly_voice("landscape aura generator mode") == "landscape origenerator mode"

    def test_dead_genau_phrases_removed(self):
        for phrase in ("enable genau", "disable genau"):
            assert phrase not in VOICE_COMMANDS

    def test_kino_mode_is_said_of_a_side_or_of_both(self):
        assert VOICE_COMMANDS["main kino mode"] == "main_kino_activate"
        assert VOICE_COMMANDS["satellite kino mode"] == "satellites_kino_activate"
        assert VOICE_COMMANDS["kino mode"] == "kino_activate"
        for retired in ("now now", "now mode", "hybrid", "hybrid mode", "player mode", "v l c",
                        "video mode", "main video mode", "satellite video mode",
                        "satellites video mode"):
            assert retired not in VOICE_COMMANDS, retired

    def test_main_funestra_version_is_spoken_like_every_other_cycle_axis(self):
        """"version" is an axis like "action" and "seed": the bare word cycles
        it, and so does an explicit verb up front."""
        for phrase in ("version", "cycle version", "next version", "change version"):
            assert VOICE_COMMANDS[phrase] == "main_player_cycle_version"

    def test_a_side_version_is_said_of_that_side_and_never_bare(self):
        """The bare word is the Main Funestra's own axis, so a satellite's
        renditions are asked for by side — in either word order, "both" with
        them, as every other satellite action is."""
        for funestra in ("portrait", "landscape", "both"):
            assert VOICE_COMMANDS[f"{funestra} version"] == f"{funestra}_cycle_version"
            assert VOICE_COMMANDS[f"version {funestra}"] == f"{funestra}_cycle_version"
        assert VOICE_COMMANDS["version"] == "main_player_cycle_version"

    def test_the_main_funestras_version_is_said_of_it_as_a_satellites_is(self):
        assert VOICE_COMMANDS["main version"] == VOICE_COMMANDS["version main"] == (
            "main_player_cycle_version")

    def test_the_main_funestras_loop_is_ended_by_naming_it_as_a_satellites_is(self):
        for words in ("end loop", "no loop", "loop off"):
            assert VOICE_COMMANDS[f"main {words}"] == VOICE_COMMANDS[f"{words} main"] == (
                "main_player_loop_cancel")

    def test_main_funestra_length_phrases(self):
        assert VOICE_COMMANDS["clips"] == "main_player_length_clips"
        assert VOICE_COMMANDS["full"] == "main_player_length_full"
        assert VOICE_COMMANDS["mixed"] == "main_player_length_mixed"

    def test_the_length_words_from_before_the_rename_are_gone(self):
        assert "shorts" not in VOICE_COMMANDS
        assert "full length" not in VOICE_COMMANDS

    def test_end_compilation_leaves_without_naming_a_length(self):
        """"compilation" gets you in; this gets you out, back to whichever length
        mode was feeding the playlist before — the same shape as "end loop"."""
        assert VOICE_COMMANDS["end compilation"] == "main_player_end_compilation"

    def test_main_reset_returns_the_playlist_to_the_default_browse(self):
        """"reset" means for the Main Funestra what it means for a satellite — drop
        whatever is narrowing the playlist — and it is order-agnostic like the rest
        of that grid.

        Its own command rather than a bare "length mixed" forward, because half of
        what narrows the Main Funestra is the F-mode flag, which is the
        orchestrator's and not the Main Funestra's to hear about.
        """
        for phrase in ("main reset", "reset main"):
            assert VOICE_COMMANDS[phrase] == "main_reset"

    def test_satellite_grid_supports_both_orders(self):
        """Each satellite action works BARE (→ active side) and with a side word
        in EITHER order: "portrait lock" and "lock portrait" are equivalent."""
        actions = {
            "lock": "lock_on",
            "unlock": "lock_off",
            "next": "next",
            "previous": "prev",
            "weird": "trash",
            "action": "cycle_action",
            "scene": "cycle_action",  # scene == action
            "seed": "cycle_seed",
        }
        for word, act in actions.items():
            assert VOICE_COMMANDS[word] == f"active_{act}"
            for side in ("portrait", "landscape", "both"):
                target = f"{side}_{act}"
                assert VOICE_COMMANDS[f"{side} {word}"] == target  # side first
                assert VOICE_COMMANDS[f"{word} {side}"] == target  # side last

    def test_f_mode_is_sayable_per_funestra_in_either_order(self):
        """Every Funestra has its own F-mode, so each is sayable by naming it — in
        either order, like the rest of the grid.  "main" names the Funestra on the
        shared slot and "both" means the two satellites."""
        for word, act in (("f mode", "fmode"),
                          ("f mode on", "fmode_on"),
                          ("f mode off", "fmode_off")):
            for side in ("portrait", "landscape", "both", "main"):
                assert VOICE_COMMANDS[f"{side} {word}"] == f"{side}_{act}"
                assert VOICE_COMMANDS[f"{word} {side}"] == f"{side}_{act}"

    def test_bare_f_mode_reaches_the_active_funestra(self):
        """Bare means the active Funestra here as it does everywhere else in the
        grid — "lock", "next", "weird" all resolve that way, and F-mode reading
        as the whole room instead is what made a spoken "f mode" report
        "enabled" over a room that already looked narrowed."""
        assert VOICE_COMMANDS["f mode"] == "active_fmode"
        assert VOICE_COMMANDS["f mode on"] == "active_fmode_on"
        assert VOICE_COMMANDS["f mode off"] == "active_fmode_off"

    def test_all_f_mode_is_the_whole_room_in_either_order(self):
        """The whole-room gesture keeps a phrase of its own — "all" joins the
        side words, and reads in either order like every one of them."""
        for word, act in (("f mode", "fmode_toggle"),
                          ("f mode on", "fmode_on"),
                          ("f mode off", "fmode_off")):
            assert VOICE_COMMANDS[f"all {word}"] == act
            assert VOICE_COMMANDS[f"{word} all"] == act

    def test_cycle_verb_phrases_map_to_the_active_cycle(self):
        """"cycle / next / change <axis>" are extra active-side spoken forms for
        the cycle commands, and "scene" reads as "action"."""
        for phrase in ("cycle action", "next action", "change action",
                       "cycle scene", "next scene", "change scene"):
            assert VOICE_COMMANDS[phrase] == "active_cycle_action"
        for phrase in ("cycle seed", "next seed", "change seed"):
            assert VOICE_COMMANDS[phrase] == "active_cycle_seed"

    def test_loop_family_phrases(self):
        """The grid's loop names ("loop actions" / "loop seeds"), the equivalent
        singular/reversed forms, "loop scene(s)" (scene == action), and "no loop"
        / "loop off" to end one — all sided in either order like the rest."""
        for phrase in ("loop actions", "loop action", "action loop", "loop scenes", "loop scene"):
            assert VOICE_COMMANDS[phrase] == "active_action_loop"
        for phrase in ("loop seeds", "loop seed", "seed loop"):
            assert VOICE_COMMANDS[phrase] == "active_seed_loop"
        for phrase in ("no loop", "loop off"):
            assert VOICE_COMMANDS[phrase] == "active_no_loop"
        assert VOICE_COMMANDS["portrait loop actions"] == "portrait_action_loop"
        assert VOICE_COMMANDS["loop actions portrait"] == "portrait_action_loop"
        assert VOICE_COMMANDS["both no loop"] == "both_no_loop"
        assert VOICE_COMMANDS["more seeds"] == "active_more_seeds"
        assert VOICE_COMMANDS["portrait more seeds"] == "portrait_more_seeds"
        # "widen (the) net" are spoken synonyms for "more seeds".
        assert VOICE_COMMANDS["widen net"] == "active_more_seeds"
        assert VOICE_COMMANDS["widen the net"] == "active_more_seeds"
        assert VOICE_COMMANDS["portrait widen the net"] == "portrait_more_seeds"

    def test_grid_lock_scopes_are_aliases_of_existing_commands(self):
        """The grid's lock scopes collapse onto commands that already exist,
        since every satellite playlist runs repeat-all: "lock seed" is the action
        loop and "lock type" the seed loop."""
        assert VOICE_COMMANDS["lock seed"] == "active_action_loop"
        assert VOICE_COMMANDS["lock type"] == "active_seed_loop"
        # sided, either order, like the rest of the grid
        assert VOICE_COMMANDS["portrait lock seed"] == "portrait_action_loop"
        assert VOICE_COMMANDS["lock type landscape"] == "landscape_seed_loop"

    def test_the_scope_named_all_is_gone_from_the_grid(self):
        """"lock all" and "loop all" read "all" as everything pinning the clip,
        where the room's other "all" — "all f mode" — means all Funestras.  Nothing
        in the phrase says which sense is meant, and both were second spellings
        of "lock" and "reset", so they went instead of being disambiguated."""
        for phrase in ("lock all", "loop all", "same all"):
            assert phrase not in VOICE_COMMANDS
            for side in ("portrait", "landscape", "both"):
                assert f"{side} {phrase}" not in VOICE_COMMANDS
                assert f"{phrase} {side}" not in VOICE_COMMANDS
        # What they said is still sayable, under the names the grid already used.
        assert VOICE_COMMANDS["lock"] == "active_lock_on"
        assert VOICE_COMMANDS["reset"] == "active_reset"
        # The surviving "all" is the whole-room one, and it means all Funestras.
        assert VOICE_COMMANDS["all f mode"] == "fmode_toggle"

    def test_main_nav_phrases_both_orders(self):
        """The Main Funestra joins the grid for navigation, in either order.  Bare
        "next"/"previous" reach it via the active side."""
        assert VOICE_COMMANDS["main next"] == "main_next"
        assert VOICE_COMMANDS["next main"] == "main_next"
        assert VOICE_COMMANDS["main previous"] == "main_prev"
        assert VOICE_COMMANDS["previous main"] == "main_prev"

    def test_main_lock_phrases_both_orders(self):
        """The Main Funestra's lock joins that grid too, and says there what it says
        on a satellite: hold the video on screen, or let its end walk the playlist.
        Bare "lock"/"unlock" reach it via the active side."""
        assert VOICE_COMMANDS["main lock"] == "main_lock_on"
        assert VOICE_COMMANDS["lock main"] == "main_lock_on"
        assert VOICE_COMMANDS["main unlock"] == "main_lock_off"
        assert VOICE_COMMANDS["unlock main"] == "main_lock_off"

    def test_primary_is_no_longer_a_spoken_word_for_the_funestra(self):
        """It names a monitor in this room — the Main Funestra and the secondary — and one
        word cannot be both a screen and a Funestra, so the old synonym is gone rather
        than kept alongside "main"."""
        spoken = {phrase for phrase in VOICE_COMMANDS if "primary" in phrase}

        assert spoken == set()

    def test_mode_named_navigation_both_orders(self):
        """A mode's name + next/previous (either order) navigates its Funestra:
        Kino drives the main slot, Genau its own flick."""
        for base, (next_command, previous_command) in (
                ("kino", ("main_next", "main_prev")),
                ("genau", ("genau_next_flick", "genau_prev_flick")),
                ("go now", ("genau_next_flick", "genau_prev_flick"))):
            assert VOICE_COMMANDS[f"{base} next"] == next_command
            assert VOICE_COMMANDS[f"next {base}"] == next_command
            assert VOICE_COMMANDS[f"{base} previous"] == previous_command
            assert VOICE_COMMANDS[f"previous {base}"] == previous_command
        for retired in ("video next", "next video", "video previous", "previous video"):
            assert retired not in VOICE_COMMANDS, retired

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

    def test_spoken_speed_ex_phrases_cover_every_stop(self):
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
            for side in ("portrait", "landscape", "both"):
                assert VOICE_COMMANDS[f"{side} {phrase}"] == f"{side}_{act}"
                assert VOICE_COMMANDS[f"{phrase} {side}"] == f"{side}_{act}"
            assert VOICE_COMMANDS[f"main {phrase}"] == f"main_player_{act}"
            assert VOICE_COMMANDS[f"{phrase} main"] == f"main_player_{act}"


def test_group_commands_join_the_order_agnostic_grid():
    """The group actions are order-agnostic in both words: "action loop" and
    "loop action" are equivalent, each works bare (active side) and with a side
    word in either order, like the other satellite actions."""
    for words, act in {
        ("action loop", "loop action"): "action_loop",
        ("seed loop", "loop seed"): "seed_loop",
        # "filter" is a synonym for the whole two-word phrase, so it joins the
        # grid the same way: bare, sided, and sided in either order.
        ("lock action", "action lock", "filter"): "lock_action",
    }.items():
        for word in words:
            assert VOICE_COMMANDS[word] == f"active_{act}"
            for side in ("portrait", "landscape", "both"):
                assert VOICE_COMMANDS[f"{side} {word}"] == f"{side}_{act}"
                assert VOICE_COMMANDS[f"{word} {side}"] == f"{side}_{act}"


def test_resume_and_unpause_are_synonyms_for_play():
    assert VOICE_COMMANDS["resume"] == "play"
    assert VOICE_COMMANDS["un pause"] == "play"  # recognizer form of "unpause"
    assert "unpause" not in VOICE_COMMANDS  # single OOV token — never a phrase


def test_shuffle_joins_the_order_agnostic_satellite_grid():
    """Shuffle is sided like its counterpart Latest, so a single satellite can be
    reshuffled without disturbing the other."""
    assert VOICE_COMMANDS["shuffle"] == "active_shuffle"
    for side in ("portrait", "landscape", "both"):
        assert VOICE_COMMANDS[f"{side} shuffle"] == f"{side}_shuffle"
        assert VOICE_COMMANDS[f"shuffle {side}"] == f"{side}_shuffle"


def test_skip_and_back_join_the_grid_for_every_funestra():
    """Ten seconds either way is on every Funestra's HUD now, so the words for it
    are said the way every other shared action is: bare to the Funestra last
    addressed, or with the Funestra named either side of them."""
    for word, act in (("skip", "nudge_next"), ("back", "nudge_prev")):
        assert VOICE_COMMANDS[word] == f"active_{act}"
        for side in ("portrait", "landscape", "both", "main"):
            assert VOICE_COMMANDS[f"{side} {word}"] == f"{side}_{act}"
            assert VOICE_COMMANDS[f"{word} {side}"] == f"{side}_{act}"


def test_reset_joins_the_order_agnostic_satellite_grid():
    assert VOICE_COMMANDS["reset"] == "active_reset"
    for side in ("portrait", "landscape", "both"):
        assert VOICE_COMMANDS[f"{side} reset"] == f"{side}_reset"
        assert VOICE_COMMANDS[f"reset {side}"] == f"{side}_reset"


def test_reset_all_reaches_every_funestra_in_either_order():
    for phrase in ("reset all", "all reset"):
        assert VOICE_COMMANDS[phrase] == "all_reset"


def test_wrong_action_joins_the_order_agnostic_satellite_grid():
    """"Wrong action" says the clip on screen is labeled as doing the wrong
    thing.  It is about one clip, like "weird", so it is sided like one: bare it
    reaches the side last addressed, and a side word works in either order."""
    assert VOICE_COMMANDS["wrong action"] == "active_wrong_action"
    for side in ("portrait", "landscape", "both"):
        assert VOICE_COMMANDS[f"{side} wrong action"] == f"{side}_wrong_action"
        assert VOICE_COMMANDS[f"wrong action {side}"] == f"{side}_wrong_action"
    # …and it must not disturb the bare "action", which still cycles.
    assert VOICE_COMMANDS["action"] == "active_cycle_action"


def test_group_commands_do_not_shadow_the_single_word_actions():
    # "lock action" must not disturb "lock"/"action"; "action loop" not "loop".
    assert VOICE_COMMANDS["lock"] == "active_lock_on"
    assert VOICE_COMMANDS["action"] == "active_cycle_action"
    assert VOICE_COMMANDS["seed"] == "active_cycle_seed"
    assert VOICE_COMMANDS["loop"] == "main_player_record_up"
    # "filter" (put one on) must not disturb the phrases that drop one.
    assert VOICE_COMMANDS["no filter"] == "active_no_filter"
    assert VOICE_COMMANDS["filter off"] == "active_no_filter"


def test_voice_commands_include_generated_filter_phrases():
    """Every act the overlay carries — not whichever happens to sort first —
    has its phrases in the grammar, checked against the same loader the
    grammar was generated from so the test is machine-independent."""
    acts = load_acts()
    assert acts
    for query, forms in acts.items():
        for form in forms:
            assert VOICE_COMMANDS[f"portrait {form}"] == set_command("portrait", query)
            assert VOICE_COMMANDS[form] == set_command("both", query)


def test_clearing_a_filter_scopes_like_every_other_satellite_action():
    """It used to be "clear portrait" — the side word standing where an action's
    own words stand everywhere else in the grid.  Now it is the grid's own
    "no filter" under two more names, so the side word goes where it always does
    and bare reaches the side last navigated rather than always both."""
    for phrase in ("no filter", "filter off", "clear filter", "show everything"):
        assert VOICE_COMMANDS[phrase] == "active_no_filter"
        assert VOICE_COMMANDS[f"portrait {phrase}"] == "portrait_no_filter"
        assert VOICE_COMMANDS[f"{phrase} landscape"] == "landscape_no_filter"
        assert VOICE_COMMANDS[f"both {phrase}"] == "both_no_filter"
    assert "clear portrait" not in VOICE_COMMANDS
    assert "clear landscape" not in VOICE_COMMANDS


def test_filter_phrases_do_not_shadow_other_commands():
    # Every filter phrase must resolve to its filter command — i.e. no filter
    # phrase silently overrode (or was overridden by) another voice command.

    for phrase, command in filter_voice_commands().items():
        assert VOICE_COMMANDS[phrase] == command
        assert decode_filter_command(command) is not None


class TestBuildVoiceCommands:
    """The vocabulary as a value: built from explicit inputs, read-only, with
    the collision guards raising from inside the builder."""

    def test_builds_from_explicit_inputs_without_touching_the_module_global(self):
        built = build_voice_commands(
            filter_commands={"fabricated act": "filter_both_fabricated_act"},
            clip_jump_phrases=("fabricated jump phrase",),
        )
        assert built["fabricated jump phrase"] == "main_player_clip_jump"
        assert built["fabricated act"] == "filter_both_fabricated_act"
        assert "fabricated jump phrase" not in VOICE_COMMANDS
        assert built["quit"] == "quit"

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

    def test_a_filter_phrase_shadowing_an_existing_command_refuses(self):
        with pytest.raises(RuntimeError, match="collide with existing voice commands"):
            build_voice_commands(
                filter_commands={"pause": "filter_both_pause"}, clip_jump_phrases=(),
            )


_FUNESTRA_WORDS = frozenset(("portrait", "landscape", "both", "main", "all", "satellite",
                             "satellites"))


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


class TestMinimizingAHudBySpeech:
    """"minimize landscape HUD" and its way back, for each Funestra and for
    whichever one was addressed last."""

    @pytest.mark.parametrize("said", ["{verb} {hud}", "{hud} {verb}"])
    @pytest.mark.parametrize(("verb", "act"), [
        ("minimize", "hud_minimize"), ("collapse", "hud_minimize"),
        ("restore", "hud_restore"), ("maximize", "hud_restore"), ("expand", "hud_restore"),
    ])
    def test_the_verb_goes_before_or_after_the_hud_it_names(self, said, verb, act):
        assert VOICE_COMMANDS.get(said.format(verb=verb, hud="hud")) == f"active_{act}"
        for funestra in ("portrait", "landscape", "both", "main"):
            assert (VOICE_COMMANDS.get(said.format(verb=verb, hud=f"{funestra} hud"))
                    == f"{funestra}_{act}")

    def test_no_phrase_says_un_minimize(self):
        """Beside "main hud minimize", "main hud un minimize" left whisper reading
        neither from a recording of the first."""
        assert [phrase for phrase in VOICE_COMMANDS if "un minimize" in phrase] == []
