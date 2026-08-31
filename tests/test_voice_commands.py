"""The spoken vocabulary and its wire format.

These tests are about :mod:`fun_time.voice_commands` alone — the generated
phrase grids and the command-file line format — and import it directly, not
through :mod:`fun_time.voice_control`, whose vosk/sounddevice runtime is
exactly the coupling the module was split out to prevent.
"""
from __future__ import annotations

import pytest

from fun_time.voice_commands import (
    build_voice_commands,
    VOICE_COMMANDS,
    format_spoken_command,
    parse_command_line,
)


class TestCommandLineFormat:
    def test_round_trips_the_utterance_start(self):
        line = format_spoken_command("portrait_lock_on", spoken_at=1234.5)
        assert parse_command_line(line) == ("portrait_lock_on", 1234.5)

    def test_an_unstamped_line_is_a_bare_command(self):
        """Hotkeys and dashboard presses are instantaneous — no back-dating."""
        assert parse_command_line("portrait_next") == ("portrait_next", None)

    def test_a_command_ending_in_an_unparseable_stamp_stays_whole(self):
        assert parse_command_line("filter_both_come @ shot") == ("filter_both_come @ shot", None)


class TestVoiceCommands:
    def test_exit_is_a_synonym_for_quit(self):
        assert VOICE_COMMANDS["exit"] == "quit"
        assert VOICE_COMMANDS["quit"] == "quit"

    def test_contains_all_static_phrases(self):
        static_phrases = {
            "quit": "quit",
            "exit": "quit",
            "pause": "pause",
            "play": "play",
            "go now": "genau_activate",
            "now now": "nau_activate",
            "now mode": "nau_activate",
            "hybrid": "hybrid_activate",
            "hybrid mode": "hybrid_activate",
            "start broker": "broker_start",
            "stop broker": "broker_stop",
            "next main": "main_next",
            "previous main": "main_prev",
            "skip": "main_nudge_next",
            "back": "main_nudge_prev",
            "record": "nau_record_down",
            "loop": "nau_record_up",
            # "end loop" is side-agnostic — it reaches Nau's own loop through the
            # active-side resolution, not by naming the player here.
            "end loop": "active_no_loop",
            # The bare axis word is the literal one; "cycle / next / change
            # version" are generated with the other cycle axes.
            "version": "nau_cycle_version",
            "shorts": "nau_length_shorts",
            "full length": "nau_length_full",
            "browse": "browse_library",
            "clip": "clipper_save",
            "save clip": "clipper_save",
            # Engine-agnostic: routed to whichever holds the OSR2, not to Genau
            # by name (the console's own marks are the by-name pair).
            "slow down": "speed_down",
            "speed down": "speed_down",
            "speed up": "speed_up",
            # …and naming the playback pins the same nudge to the video.
            "playback slow down": "nau_speed_down",
            "playback speed down": "nau_speed_down",
            "playback speed up": "nau_speed_up",
            "amp down": "genau_amplitude_down",
            "amp up": "genau_amplitude_up",
            "center down": "genau_center_down",
            "center up": "genau_center_up",
            "next shape": "genau_cycle_shape",
            "previous shape": "genau_cycle_shape_prev",
            "go now auto": "genau_toggle_auto",
            "cruise control": "genau_toggle_cruise",
            "cruise on": "genau_cruise_on",
            "cruise off": "genau_cruise_off",
            "previous clip": "genau_prev_clip",
            "next clip": "genau_next_clip",
            "offset": "quarter_button",
            "voice off": "voice_off",
            "mic off": "voice_off",
        }
        for phrase, cmd in static_phrases.items():
            assert VOICE_COMMANDS[phrase] == cmd

    def test_genau_clip_phrases_are_distinct_from_the_satellite_ones(self):
        """Bare "weird" already means the active satellite, so Genau's clip
        action has to name the clip."""
        assert VOICE_COMMANDS["weird clip"] == "genau_weird_clip"
        assert VOICE_COMMANDS["weird"] == "active_trash"

    def test_holding_a_genau_clip_is_the_main_lock_and_nothing_of_its_own(self):
        """It was a phrase and a padlock beside auto advance's arming; the two
        could disagree, and the console carried a second lock next to Nau's."""
        assert "lock clip" not in VOICE_COMMANDS
        assert "auto advance" not in VOICE_COMMANDS
        assert "advance on" not in VOICE_COMMANDS
        assert "advance off" not in VOICE_COMMANDS
        assert VOICE_COMMANDS["main lock"] == "main_lock_on"

    def test_a_spoken_interval_names_the_seconds(self):
        # A spoken interval covers 1-60 seconds, single digits and compounds
        # included — the tens-only vocabulary could not hear "advance five".
        # The phrase is "clip seconds": what the number means, not the machinery.
        for word, seconds in (
            ("one", 1), ("five", 5), ("nine", 9), ("fifteen", 15),
            ("thirty", 30), ("forty five", 45), ("sixty", 60),
        ):
            assert VOICE_COMMANDS[f"clip seconds {word}"] == f"genau_clip_seconds_{seconds}"
        assert not any(p.startswith("auto advance") for p in VOICE_COMMANDS)

    def test_no_spoken_clip_interval_is_zero_seconds(self):
        """A zero-second interval would step the clip every frame."""
        assert not any(cmd == "genau_clip_seconds_0" for cmd in VOICE_COMMANDS.values())

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
        oov_words = {"genau", "nau", "hotkeys", "unmute"}
        for phrase in VOICE_COMMANDS:
            offenders = oov_words & set(phrase.split())
            assert not offenders, f"{phrase!r} uses out-of-vocabulary {sorted(offenders)}"

    def test_unmute_is_heard_as_two_words(self):
        """vosk has no "unmute" token but does have "un"; the recognizer listens
        for "un mute" and the reference shows the friendly single word."""
        assert VOICE_COMMANDS["un mute"] == "audio_unmute"
        assert "unmute" not in VOICE_COMMANDS

    def test_reference_popup_phrases_toggle_and_close_help(self):
        # Several spoken names toggle the hotkeys & voice reference popup; the
        # same names prefixed with "close" only dismiss it.  vosk has no
        # "hotkeys" token, so it listens for "hot keys" (two words).
        for phrase in ("help", "reference", "hot keys", "voice commands"):
            assert VOICE_COMMANDS[phrase] == "help_reference"
            assert VOICE_COMMANDS[f"close {phrase}"] == "help_reference_close"
        assert "hotkeys" not in VOICE_COMMANDS  # OOV single token — never a recognizer phrase

    def test_go_now_activates_genau(self):
        # Recognizer phrase stays "go now"; the reference displays it as "genau".
        assert VOICE_COMMANDS["go now"] == "genau_activate"

    def test_dead_genau_phrases_removed(self):
        for phrase in ("enable genau", "disable genau"):
            assert phrase not in VOICE_COMMANDS

    def test_video_activates_nau(self):
        assert VOICE_COMMANDS["now now"] == "nau_activate"
        assert "v l c" not in VOICE_COMMANDS

    def test_hybrid_activates_hybrid(self):
        assert VOICE_COMMANDS["hybrid"] == "hybrid_activate"

    def test_nau_version_is_spoken_like_every_other_cycle_axis(self):
        """"version" is an axis like "action" and "seed": the bare word cycles
        it, and so does an explicit verb up front."""
        for phrase in ("version", "cycle version", "next version", "change version"):
            assert VOICE_COMMANDS[phrase] == "nau_cycle_version"

    def test_nau_length_phrases(self):
        assert VOICE_COMMANDS["shorts"] == "nau_length_shorts"
        assert VOICE_COMMANDS["full length"] == "nau_length_full"
        assert VOICE_COMMANDS["mixed"] == "nau_length_mixed"

    def test_end_compilation_leaves_without_naming_a_length(self):
        """"compilation" gets you in; this gets you out, back to whichever length
        mode was feeding the playlist before — the same shape as "end loop"."""
        assert VOICE_COMMANDS["end compilation"] == "nau_end_compilation"

    def test_main_reset_returns_the_playlist_to_the_default_browse(self):
        """"reset" means for the main player what it means for a satellite — drop
        whatever is narrowing the playlist — and it is order-agnostic like the rest
        of that grid.

        Its own command rather than a bare "length mixed" forward, because half of
        what narrows the main player is the F-mode flag, which is the
        orchestrator's and not Nau's to hear about.
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

    def test_f_mode_is_sayable_per_player_in_either_order(self):
        """Every player has its own F-mode, so each is sayable by naming it — in
        either order, like the rest of the grid.  "main" names the player on the
        shared slot and "both" means the two satellites."""
        for word, act in (("f mode", "fmode"),
                          ("f mode on", "fmode_on"),
                          ("f mode off", "fmode_off")):
            for side in ("portrait", "landscape", "both", "main"):
                assert VOICE_COMMANDS[f"{side} {word}"] == f"{side}_{act}"
                assert VOICE_COMMANDS[f"{word} {side}"] == f"{side}_{act}"

    def test_bare_f_mode_reaches_the_active_player(self):
        """Bare means the active player here as it does everywhere else in the
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
        where the room's other "all" — "all f mode" — means all players.  Nothing
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
        # The surviving "all" is the whole-room one, and it means all players.
        assert VOICE_COMMANDS["all f mode"] == "fmode_toggle"

    def test_main_nav_phrases_both_orders(self):
        """The main player joins the grid for navigation, in either order.  Bare
        "next"/"previous" reach it via the active side."""
        assert VOICE_COMMANDS["main next"] == "main_next"
        assert VOICE_COMMANDS["next main"] == "main_next"
        assert VOICE_COMMANDS["main previous"] == "main_prev"
        assert VOICE_COMMANDS["previous main"] == "main_prev"

    def test_main_lock_phrases_both_orders(self):
        """The main player's lock joins that grid too, and says there what it says
        on a satellite: hold the video on screen, or let its end walk the playlist.
        Bare "lock"/"unlock" reach it via the active side."""
        assert VOICE_COMMANDS["main lock"] == "main_lock_on"
        assert VOICE_COMMANDS["lock main"] == "main_lock_on"
        assert VOICE_COMMANDS["main unlock"] == "main_lock_off"
        assert VOICE_COMMANDS["unlock main"] == "main_lock_off"

    def test_primary_is_no_longer_a_spoken_word_for_the_player(self):
        """It names a monitor in this room — the main player and the secondary — and one
        word cannot be both a screen and a player, so the old synonym is gone rather
        than kept alongside "main"."""
        spoken = {phrase for phrase in VOICE_COMMANDS if "primary" in phrase}

        assert spoken == set()

    def test_mode_named_navigation_both_orders(self):
        """A mode's name + next/previous (either order) navigates its player:
        Nau/Hybrid drive the main slot, Genau its own clip.  vosk can't hear
        "nau"/"genau", so the recognizer uses the "now mode"/"go now" sound-alikes."""
        # Nau (recognizer "now mode") and Hybrid both drive the main slot.
        for base in ("now mode", "hybrid"):
            assert VOICE_COMMANDS[f"{base} next"] == "main_next"
            assert VOICE_COMMANDS[f"next {base}"] == "main_next"
            assert VOICE_COMMANDS[f"{base} previous"] == "main_prev"
            assert VOICE_COMMANDS[f"previous {base}"] == "main_prev"
        # Genau (recognizer "go now") steps its own clip.
        assert VOICE_COMMANDS["go now next"] == "genau_next_clip"
        assert VOICE_COMMANDS["next go now"] == "genau_next_clip"
        assert VOICE_COMMANDS["go now previous"] == "genau_prev_clip"
        assert VOICE_COMMANDS["previous go now"] == "genau_prev_clip"

    def test_contains_numeric_amp_phrases(self):
        assert VOICE_COMMANDS["amp fifty"] == "genau_amp_50"
        assert VOICE_COMMANDS["amp zero"] == "genau_amp_0"
        assert VOICE_COMMANDS["amp one hundred"] == "genau_amp_100"

    def test_contains_numeric_center_phrases(self):
        assert VOICE_COMMANDS["center eighty"] == "genau_center_80"

    def test_contains_numeric_speed_phrases(self):
        assert VOICE_COMMANDS["speed thirty"] == "genau_speed_30"

    def test_min_max_extreme_phrases(self):
        assert VOICE_COMMANDS["min amp"] == "genau_amp_0"
        assert VOICE_COMMANDS["max amp"] == "genau_amp_100"
        assert VOICE_COMMANDS["min center"] == "genau_center_0"
        assert VOICE_COMMANDS["max center"] == "genau_center_100"
        # Speed min/max route to the active engine (Nau video or Genau), not
        # Genau-only like the amp/center extremes.
        assert VOICE_COMMANDS["min speed"] == "speed_min"
        assert VOICE_COMMANDS["max speed"] == "speed_max"

    def test_nau_multiplier_speed_phrases(self):
        assert VOICE_COMMANDS["half speed"] == "nau_speed_50"
        assert VOICE_COMMANDS["normal speed"] == "nau_speed_100"
        assert VOICE_COMMANDS["one and a half speed"] == "nau_speed_150"
        assert VOICE_COMMANDS["double speed"] == "nau_speed_200"

    def test_spoken_speed_ex_phrases_cover_every_stop(self):
        assert VOICE_COMMANDS["speed point two five ex"] == "nau_speed_25"
        assert VOICE_COMMANDS["speed one ex"] == "nau_speed_100"
        assert VOICE_COMMANDS["speed one point two five ex"] == "nau_speed_125"
        assert VOICE_COMMANDS["speed one point seven five ex"] == "nau_speed_175"
        assert VOICE_COMMANDS["speed two ex"] == "nau_speed_200"

    def test_reset_speed_snaps_to_normal(self):
        assert VOICE_COMMANDS["reset speed"] == "nau_speed_100"


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


def test_reset_joins_the_order_agnostic_satellite_grid():
    assert VOICE_COMMANDS["reset"] == "active_reset"
    for side in ("portrait", "landscape", "both"):
        assert VOICE_COMMANDS[f"{side} reset"] == f"{side}_reset"
        assert VOICE_COMMANDS[f"reset {side}"] == f"{side}_reset"


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
    assert VOICE_COMMANDS["loop"] == "nau_record_up"
    # "filter" (put one on) must not disturb the phrases that drop one.
    assert VOICE_COMMANDS["no filter"] == "active_no_filter"
    assert VOICE_COMMANDS["filter off"] == "active_no_filter"


def test_voice_commands_include_generated_filter_phrases():
    """Every act the overlay carries — not whichever happens to sort first —
    has its phrases in the grammar, checked against the same loader the
    grammar was generated from so the test is machine-independent."""
    from fun_time.filter_vocab import load_filter_acts, set_command

    acts = load_filter_acts()
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
    from fun_time.filter_vocab import decode_filter_command, filter_voice_commands

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
        assert built["fabricated jump phrase"] == "nau_clip_jump"
        assert built["fabricated act"] == "filter_both_fabricated_act"
        assert "fabricated jump phrase" not in VOICE_COMMANDS
        assert built["quit"] == "quit"

    def test_the_built_vocabulary_is_read_only(self):
        built = build_voice_commands(filter_commands={}, clip_jump_phrases=())
        with pytest.raises(TypeError):
            built["fabricated"] = "quit"

    def test_a_hosted_phrase_colliding_with_a_session_command_refuses(self):
        with pytest.raises(RuntimeError, match="collides with a session command"):
            build_voice_commands(
                filter_commands={}, clip_jump_phrases=(),
                origenerator_phrases=("next",),
            )

    def test_a_filter_phrase_shadowing_an_existing_command_refuses(self):
        with pytest.raises(RuntimeError, match="collide with existing voice commands"):
            build_voice_commands(
                filter_commands={"pause": "filter_both_pause"}, clip_jump_phrases=(),
            )
