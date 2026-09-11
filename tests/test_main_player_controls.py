from __future__ import annotations

import threading
from pathlib import Path

import pytest

from main_player.controls import SEEK_STEP_MS, SPEED_STEP, VERBS, MainPlayerControls, apply_command
from main_player.session import MAX_SPEED_RATE, MIN_SPEED_RATE


class SpySession:
    def __init__(self, loop_state: str = "normal") -> None:
        self.calls: list[tuple] = []
        self.loop_state = loop_state

    def step(self, delta: int) -> None:
        self.calls.append(("step", delta))

    def seek_by(self, delta_ms: float) -> None:
        self.calls.append(("seek_by", delta_ms))

    def record_down(self) -> None:
        self.calls.append(("record_down",))

    def record_up(self) -> None:
        self.calls.append(("record_up",))

    def loop_cancel(self) -> None:
        self.calls.append(("loop_cancel",))

    def restore_loop(self, in_ms: int, out_ms: int) -> None:
        self.calls.append(("restore_loop", in_ms, out_ms))

    def toggle_lock(self) -> None:
        self.calls.append(("toggle_lock",))

    def set_locked(self, locked: bool) -> None:
        self.calls.append(("set_locked", locked))

    def cycle_version(self) -> None:
        self.calls.append(("cycle_version",))

    def play_file(self, video: Path, funscript: Path | None) -> None:
        self.calls.append(("play_file", video, funscript))

    def set_tcode_enabled(self, enabled: bool) -> None:
        self.calls.append(("set_tcode_enabled", enabled))

    def adjust_speed(self, delta: float) -> None:
        self.calls.append(("adjust_speed", delta))

    def set_speed(self, speed: float) -> None:
        self.calls.append(("set_speed", speed))

    def set_volume(self, volume: int) -> None:
        self.calls.append(("set_volume", volume))


class SpyModes:
    """Stands in for :class:`main_player.modes.Modes` -- the length filter, the way out
    of a compilation and Fun Time's own narrowing are one object in the app, so
    they are one collaborator here."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def toggle_length(self) -> None:
        self.calls.append(("toggle_length",))

    def set_length(self, mode: str) -> None:
        self.calls.append(("set_length", mode))

    def end_compilation(self) -> None:
        self.calls.append(("end_compilation",))

    def set_f_mode(self, on: bool) -> None:
        self.calls.append(("set_f_mode", on))


class SpyJumps:
    """Stands in for :class:`main_player.clip_jumps.ClipJumps`."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def play_compilation(self) -> None:
        self.calls.append(("play_compilation",))

    def play_full_vid(self) -> None:
        self.calls.append(("play_full_vid",))

    def play_clip_jump(self) -> None:
        self.calls.append(("play_clip_jump",))


class SpyFunscriptJumps:
    """Stands in for :class:`main_player.funscript_jumps.FunscriptJumps`."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def jump_to_funscript(self) -> None:
        self.calls.append(("jump_to_funscript",))

    def next_funscripted(self) -> None:
        self.calls.append(("next_funscripted",))

# Every verb that reaches past the session, the collaborator it belongs to and
# the method it calls there.  A build that did not wire that collaborator
# refuses the verb; the triple is what says a refused verb reached no neighbour
# either.
_COLLABORATOR_VERBS = [
    ("TOGGLE_LENGTH_MODE", "modes", "toggle_length"),
    ("SET_LENGTH_MODE shorts", "modes", "set_length"),
    ("END_COMPILATION", "modes", "end_compilation"),
    ("SET_F_MODE 1", "modes", "set_f_mode"),
    ("PLAY_COMPILATION", "jumps", "play_compilation"),
    ("PLAY_FULL_VID", "jumps", "play_full_vid"),
    ("PLAY_CLIP_JUMP", "jumps", "play_clip_jump"),
    ("JUMP_TO_FUNSCRIPT", "funscript_jumps", "jump_to_funscript"),
    ("NEXT_FUNSCRIPTED", "funscript_jumps", "next_funscripted"),
]


class TestAnUnhandledCommand:
    """The dispatcher says so itself, because it is the only thing that knows.

    Fun Time is written against this: `command_dispatch.py` routes
    CYCLE_PROJECTION and RECENTER to the main player's channel with the comment "so the
    desktop main player simply logs it as unknown" -- verbs only FunTimeVR's player
    answers. Before this they were dropped in silence.
    """

    def test_an_unknown_verb_is_named_on_the_log(self, caplog):
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command("CYCLE_PROJECTION", MainPlayerControls(SpySession()))

        assert "CYCLE_PROJECTION" in caplog.text

    def test_a_verb_this_build_did_not_wire_is_named_too(self, caplog):
        """A collaborator the app left out is as unanswerable as a typo, and
        just as much worth seeing."""
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command("TOGGLE_LENGTH_MODE", MainPlayerControls(SpySession()))

        assert "TOGGLE_LENGTH_MODE" in caplog.text

    def test_the_one_verb_that_went_quiet_unwired_is_named_too(self, caplog):
        """RELOAD_PLAYLIST answered "handled" with its callback absent while
        the other eleven collaborator verbs answer False and get named here
        (bug 65)."""
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command("RELOAD_PLAYLIST", MainPlayerControls(SpySession()))

        assert "RELOAD_PLAYLIST" in caplog.text

    def test_a_verb_it_acts_on_says_nothing(self, caplog):
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command("NEXT", MainPlayerControls(SpySession()))

        assert caplog.records == []


class TestApplyCommand:
    def test_it_answers_nothing_because_nothing_asks(self):
        """main_player/app.py, the one production caller, calls it as a statement.

        It used to return True/False for "did I understand this", and three
        helper docstrings promised a caller that reported an unhandled verb.
        There has never been one: an unknown or malformed verb is dropped in
        silence either way.
        """
        assert apply_command("NEXT", MainPlayerControls(SpySession())) is None
        assert apply_command("NOT_A_VERB", MainPlayerControls(SpySession())) is None

    def test_next_and_prev_step(self):
        session = SpySession()

        apply_command("NEXT", MainPlayerControls(session))
        apply_command("PREV", MainPlayerControls(session))

        assert session.calls == [("step", 1), ("step", -1)]

    def test_keyword_is_case_insensitive(self):
        session = SpySession()

        apply_command("next", MainPlayerControls(session))

        assert session.calls == [("step", 1)]

    def test_seek_commands(self):
        session = SpySession()

        apply_command("SEEK_FWD", MainPlayerControls(session))
        apply_command("SEEK_BACK", MainPlayerControls(session))

        assert session.calls == [
            ("seek_by", SEEK_STEP_MS), ("seek_by", -SEEK_STEP_MS),
        ]

    def test_speed_commands(self):
        session = SpySession()

        apply_command("SPEED_UP", MainPlayerControls(session))
        apply_command("SPEED_DOWN", MainPlayerControls(session))

        assert session.calls == [
            ("adjust_speed", SPEED_STEP), ("adjust_speed", -SPEED_STEP),
        ]

    def test_set_speed_absolute_and_extremes(self):
        session = SpySession()

        apply_command("SET_SPEED min", MainPlayerControls(session))
        apply_command("SET_SPEED max", MainPlayerControls(session))
        apply_command("SET_SPEED 1.5", MainPlayerControls(session))

        assert session.calls == [
            ("set_speed", MIN_SPEED_RATE),
            ("set_speed", MAX_SPEED_RATE),
            ("set_speed", 1.5),
        ]

    def test_a_set_speed_it_cannot_read_leaves_the_rate_alone(self):
        session = SpySession()

        apply_command("SET_SPEED", MainPlayerControls(session))
        apply_command("SET_SPEED fast", MainPlayerControls(session))

        assert session.calls == []

    def test_a_set_volume_it_cannot_read_leaves_the_level_alone(self):
        session = SpySession()
        shown = []

        apply_command(
            "SET_VOLUME loud", MainPlayerControls(session, set_volume_hud=lambda *a: shown.append(a)))

        assert (session.calls, shown) == ([], [])

    def test_a_build_with_nowhere_to_draw_the_level_refuses_it(self):
        """The level and the chip are one control: a player told what the sound
        is doing and unable to show it would move a slider nobody can see."""
        session = SpySession()

        apply_command("SET_VOLUME 40", MainPlayerControls(session))

        assert session.calls == []

    def test_set_volume_takes_the_mute_as_a_fact_of_its_own(self):
        """Fun Time publishes a mute to its audio sinks as a level of zero, which
        is all a sink needs and not enough to *draw*: silent and turned-all-the-way
        down look the same.  So the level and the mute both come, and the audible
        loudness is derived here."""
        session = SpySession()
        shown = []

        apply_command("SET_VOLUME 70 1", MainPlayerControls(session, set_volume_hud=lambda *a: shown.append(a)))

        assert session.calls == [("set_volume", 0)], "muted plays silent"
        assert shown == [(70, True)], "…but the control still shows where it was set"

    def test_set_volume_unmuted_plays_and_shows_the_same_level(self):
        session = SpySession()
        shown = []

        apply_command("SET_VOLUME 70 0", MainPlayerControls(session, set_volume_hud=lambda *a: shown.append(a)))

        assert session.calls == [("set_volume", 70)]
        assert shown == [(70, False)]

    def test_set_volume_without_a_mute_flag_is_not_muted(self):
        """The one-argument form is what every caller sent before there was a
        control to draw, and it means exactly what it did then."""
        session = SpySession()
        shown = []

        apply_command("SET_VOLUME 40", MainPlayerControls(session, set_volume_hud=lambda *a: shown.append(a)))

        assert session.calls == [("set_volume", 40)]
        assert shown == [(40, False)]

    def test_record_commands(self):
        session = SpySession()

        apply_command("RECORD_DOWN", MainPlayerControls(session))
        apply_command("RECORD_UP", MainPlayerControls(session))
        apply_command("LOOP_CANCEL", MainPlayerControls(session))

        assert session.calls == [("record_down",), ("record_up",), ("loop_cancel",)]

    def test_set_loop_puts_a_range_back_without_replaying_the_gesture(self):
        """How a loop survives a restart: Fun Time reads the bounds off the
        status file, and hands them back over the video it resumed the playlist
        onto — RECORD_DOWN/RECORD_UP could not, since they mark against wherever
        the playhead happens to be."""
        session = SpySession()

        apply_command("SET_LOOP 2000 4000", MainPlayerControls(session))

        assert session.calls == [("restore_loop", 2000, 4000)]

    def test_a_set_loop_range_it_cannot_read_leaves_the_player_alone(self):
        session = SpySession()

        apply_command("SET_LOOP", MainPlayerControls(session))
        apply_command("SET_LOOP 2000", MainPlayerControls(session))
        apply_command("SET_LOOP 2000 later", MainPlayerControls(session))

        assert session.calls == []

    def test_lock_commands(self):
        """The toggle for the key and the button; the absolute pair for the two
        spoken forms, which name the state they want."""
        session = SpySession()

        apply_command("TOGGLE_LOCK", MainPlayerControls(session))
        apply_command("LOCK_ON", MainPlayerControls(session))
        apply_command("LOCK_OFF", MainPlayerControls(session))

        assert session.calls == [
            ("toggle_lock",), ("set_locked", True), ("set_locked", False),
        ]

    def test_record_tap_cycles_by_state(self):
        normal = SpySession(loop_state="normal")
        apply_command("RECORD_TAP", MainPlayerControls(normal))
        assert normal.calls == [("record_down",)]

        recording = SpySession(loop_state="recording")
        apply_command("RECORD_TAP", MainPlayerControls(recording))
        assert recording.calls == [("record_up",)]

        looping = SpySession(loop_state="looping")
        apply_command("RECORD_TAP", MainPlayerControls(looping))
        assert looping.calls == [("loop_cancel",)]

    def test_play_file_with_funscript(self):
        session = SpySession()

        apply_command("PLAY_FILE C:/Videos/My Clip.mp4\tC:/Scripts/My Clip.funscript", MainPlayerControls(session))

        assert session.calls == [(
            "play_file",
            Path("C:/Videos/My Clip.mp4"),
            Path("C:/Scripts/My Clip.funscript"),
        )]

    def test_play_file_without_funscript(self):
        session = SpySession()

        apply_command("PLAY_FILE C:/Videos/My Clip.mp4", MainPlayerControls(session))

        assert session.calls == [("play_file", Path("C:/Videos/My Clip.mp4"), None)]

    def test_cycle_version(self):
        session = SpySession()

        apply_command("CYCLE_VERSION", MainPlayerControls(session))

        assert session.calls == [("cycle_version",)]

    def test_set_tcode_enabled_zero_disables(self):
        session = SpySession()

        apply_command("SET_TCODE_ENABLED 0", MainPlayerControls(session))

        assert session.calls == [("set_tcode_enabled", False)]

    def test_set_tcode_enabled_one_enables(self):
        session = SpySession()

        apply_command("SET_TCODE_ENABLED 1", MainPlayerControls(session))

        assert session.calls == [("set_tcode_enabled", True)]

    def test_set_tcode_enabled_without_an_argument_leaves_the_driver_alone(self):
        session = SpySession()

        apply_command("SET_TCODE_ENABLED", MainPlayerControls(session))

        assert session.calls == []

    def test_reload_playlist_asks_for_the_playlist_again(self):
        """Fun Time owns the playlist file and rewrites it whenever the room's
        selection changes; this is how it says so."""
        session = SpySession()
        reloaded = []

        apply_command(
            "RELOAD_PLAYLIST", MainPlayerControls(session, reload_playlist=lambda: reloaded.append(1)))

        assert reloaded == [1]
        assert session.calls == []

    def test_the_length_filter_is_toggled_and_named(self):
        session = SpySession()
        modes = SpyModes()

        apply_command("TOGGLE_LENGTH_MODE", MainPlayerControls(session, modes=modes))
        apply_command("SET_LENGTH_MODE shorts", MainPlayerControls(session, modes=modes))

        assert modes.calls == [("toggle_length",), ("set_length", "shorts")]
        assert session.calls == []

    def test_end_compilation_goes_back_to_the_mode_that_was_running(self):
        """Leaving a compilation without having to name a length: the mode you
        were in before you entered is the one you go back to."""
        session = SpySession()
        modes = SpyModes()

        apply_command("END_COMPILATION", MainPlayerControls(session, modes=modes))

        assert modes.calls == [("end_compilation",)]
        assert session.calls == []

    def test_set_f_mode_says_the_flag_outright(self):
        """F-mode is Fun Time's flag; all the main player ever sees of it is a pre-narrowed
        playlist, which looks like any other.  So the orchestrator has to say it
        outright for the HUD to be able to."""
        session = SpySession()
        modes = SpyModes()

        apply_command("SET_F_MODE 1", MainPlayerControls(session, modes=modes))
        apply_command("SET_F_MODE 0", MainPlayerControls(session, modes=modes))

        assert modes.calls == [("set_f_mode", True), ("set_f_mode", False)]
        assert session.calls == []

    def test_the_three_ways_into_another_slice_of_the_library(self):
        """Each its own verb because each answers a different question:
        everything this video was carved from, the whole thing it was carved out
        of, and one scene from somewhere else."""
        session = SpySession()
        jumps = SpyJumps()

        for verb in ("PLAY_COMPILATION", "PLAY_FULL_VID", "PLAY_CLIP_JUMP"):
            apply_command(verb, MainPlayerControls(session, jumps=jumps))

        assert jumps.calls == [
            ("play_compilation",), ("play_full_vid",), ("play_clip_jump",)]
        assert session.calls == []

    def test_the_funscripts_own_two_moves(self):
        """Past this video's quiet stretch, or on to a video that has scripting
        at all."""
        session = SpySession()
        funscript_jumps = SpyFunscriptJumps()

        apply_command(
            "JUMP_TO_FUNSCRIPT", MainPlayerControls(session, funscript_jumps=funscript_jumps))
        apply_command(
            "NEXT_FUNSCRIPTED", MainPlayerControls(session, funscript_jumps=funscript_jumps))

        assert funscript_jumps.calls == [
            ("jump_to_funscript",), ("next_funscripted",)]
        assert session.calls == []

    @pytest.mark.parametrize("verb, collaborator, method", _COLLABORATOR_VERBS)
    def test_a_verb_reaches_that_collaborator_and_no_other(
            self, verb, collaborator, method):
        """A verb that fell through to a neighbor would show up as the wrong
        label rather than reading as a quiet no-op: all three collaborators are
        wired, so only the one named may hear anything."""
        session = SpySession()
        wired = {"modes": SpyModes(), "jumps": SpyJumps(),
                 "funscript_jumps": SpyFunscriptJumps()}

        apply_command(verb, MainPlayerControls(session, **wired))

        heard = [(name, call[0]) for name, spy in wired.items() for call in spy.calls]
        assert heard == [(collaborator, method)]
        assert session.calls == []

    @pytest.mark.parametrize(
        "verb", [v for v, _c, _m in _COLLABORATOR_VERBS] + ["RELOAD_PLAYLIST"])
    def test_a_verb_this_build_did_not_wire_touches_nothing(self, verb):
        session = SpySession()

        apply_command(verb, MainPlayerControls(session))

        assert session.calls == []

    def test_display_verbs_invoke_callback(self):
        """Whether the main player owns the main slot's rect is Fun Time's to say: in genau mode
        it hands that rect to Genau and minimizes the main player, which keeps its taskbar
        button — so the player has to be told to go black rather than sit there
        holding the frame it was paused on."""
        session = SpySession()
        states = []

        apply_command("DISPLAY_OFF", MainPlayerControls(session, set_display=states.append))
        apply_command("DISPLAY_ON", MainPlayerControls(session, set_display=states.append))

        assert states == [False, True]
        assert session.calls == [], "the display is not playback"

    def test_display_verbs_without_their_callback_do_nothing(self):
        session = SpySession()

        apply_command("DISPLAY_OFF", MainPlayerControls(session))
        apply_command("DISPLAY_ON", MainPlayerControls(session))

        assert session.calls == []

    def test_quit_sets_stop_event(self):
        session = SpySession()
        stop = threading.Event()

        apply_command("QUIT", MainPlayerControls(session, stop_event=stop))

        assert stop.is_set()

    def test_an_unknown_command_touches_nothing(self):
        session = SpySession()

        apply_command("FROBNICATE", MainPlayerControls(session))
        apply_command("", MainPlayerControls(session))

        assert session.calls == []


# Every command line the main player answers, one per verb, with fabricated arguments.
# Written out rather than read off the dispatcher: these strings are what Fun
# Time writes into main_player_cmd.txt, so a verb renamed on this side is a control
# that goes quiet on the other, and the rename has to show up as a diff in both
# repos.  One-directional on purpose -- a verb ADDED here is backward
# compatible and this says nothing about it; a verb removed or respelled is
# what it catches.
ACCEPTED_COMMANDS = [
    "NEXT", "PREV", "SEEK_FWD", "SEEK_BACK",
    "SPEED_UP", "SPEED_DOWN", "SET_SPEED 1.5", "SET_SPEED min", "SET_SPEED max",
    "SET_VOLUME 40", "SET_VOLUME 40 1",
    "RECORD_DOWN", "RECORD_UP", "RECORD_TAP", "LOOP_CANCEL", "SET_LOOP 1000 2000",
    "TOGGLE_LOCK", "LOCK_ON", "LOCK_OFF",
    "CYCLE_VERSION", "PLAY_FILE C:/example/library/videos/gamma reel.mp4",
    "RELOAD_PLAYLIST", "TOGGLE_LENGTH_MODE", "SET_LENGTH_MODE shorts",
    "PLAY_COMPILATION", "PLAY_FULL_VID", "PLAY_CLIP_JUMP",
    "JUMP_TO_FUNSCRIPT", "NEXT_FUNSCRIPTED", "END_COMPILATION",
    "SET_TCODE_ENABLED 1", "SET_F_MODE 1",
    "DISPLAY_ON", "DISPLAY_OFF",
    "QUIT",
]

def _fully_wired() -> MainPlayerControls:
    """Everything a player launched by Fun Time hands the dispatcher.

    Every verb in the contract below is refused by a build missing what it
    needs, so the snapshot only says anything about the spellings when nothing
    is missing.
    """
    return MainPlayerControls(
        SpySession(),
        stop_event=threading.Event(),
        reload_playlist=lambda: None,
        modes=SpyModes(),
        jumps=SpyJumps(),
        funscript_jumps=SpyFunscriptJumps(),
        set_volume_hud=lambda *_args: None,
        set_display=lambda *_args: None,
    )


class TestTheVerbsFunTimeCanSend:
    """The command file is an orchestrator contract in the other direction from
    the status file: Fun Time writes these words and the main player acts on them.  The
    dispatcher says so itself -- an unhandled verb is a WARNING and nothing
    else -- so a respelled verb is a control that silently stops working, which
    is exactly what a log line nobody is reading looks like.
    """

    @pytest.mark.parametrize("command", ACCEPTED_COMMANDS)
    def test_it_is_answered_rather_than_logged_as_unknown(self, command, caplog):
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command(command, _fully_wired())

        assert caplog.records == []

    @pytest.mark.parametrize(
        "command", ["NEXT 5", "QUIT now", "RECORD_TAP 1", "DISPLAY_ON 0"])
    def test_a_value_on_a_verb_that_takes_none_is_refused(self, command, caplog):
        """Half a command is not a command, and neither is one and a half.

        A sender that put a value on ``NEXT`` was asking for something this
        player does not have; stepping one video is not what it asked for, so
        answering the bare verb would act on a reading nobody wrote.  Genau's
        half of the family already refuses both directions
        (``player_core.control_registry.act``); this is the main player agreeing.
        """
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command(command, _fully_wired())

        assert command.split()[0] in caplog.text

    @pytest.mark.parametrize(
        "command",
        ["SET_SPEED", "SET_VOLUME", "SET_LOOP", "PLAY_FILE",
         "SET_LENGTH_MODE", "SET_TCODE_ENABLED", "SET_F_MODE"])
    def test_a_verb_that_wants_a_value_is_refused_without_one(self, command, caplog):
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command(command, _fully_wired())

        assert command in caplog.text

    def test_the_registry_declares_exactly_these_verbs_and_no_others(self):
        """The other leg, and the one the list above cannot walk on its own: a
        verb *added* to the registry without a line here would be a control the main player
        answers that Fun Time has never been told about, and every case above
        would still pass."""
        assert set(VERBS) == {line.split()[0] for line in ACCEPTED_COMMANDS}

    def test_a_word_it_does_not_know_is_named_on_the_log(self, caplog):
        """The control probe: without it, a dispatcher that answered everything
        would pass every case above."""
        with caplog.at_level("WARNING", logger="main_player.controls"):
            apply_command("FROBNICATE", _fully_wired())

        assert "FROBNICATE" in caplog.text
