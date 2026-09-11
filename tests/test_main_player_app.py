"""The main player's own module: what it wires together before the loop starts.

``main_player.app`` is imported inside each test rather than at module scope: importing
it pulls pygame in for real, and the view tests that replace pygame with a mock
go red inside pygame's own resource lookup if that happens before they run.  By
the time these do, those have.  ``tests/test_taskbar_identity.py`` reaches its
two names the same way and says the same thing.
"""
from __future__ import annotations

import ast
import logging
import threading
from pathlib import Path

import pytest

from main_player.cli import build_parser
from main_player.controls import apply_command
from main_player.status import status_fields


class StubSession:
    """The shape :func:`main_player.status.status_fields` reads a player through."""

    current_video = Path("C:/vids/gamma reel.mp4")
    position_ms = 12345.6
    duration_ms = 60000.0
    has_funscript = True
    funscript_resting = False
    loop_state = "normal"
    loop_bounds = None
    is_paused = False
    locked = True


class FakeGate:
    """The trace's latch, as the status file asks it: one touch, or none."""

    def __init__(self, touch: int | None = None) -> None:
        self.touch = touch
        self.asked = 0

    def handoff_touch(self) -> int | None:
        self.asked += 1
        return self.touch


def _writer(args, gate):
    from main_player.app import _status_writer
    return _status_writer(args, gate)


def _args(status_file: Path):
    return build_parser({}).parse_args(["--status-file", str(status_file)])


class TestTheStatusFileMainPlayerPublishes:
    """The reverse leg of the orchestrator channel: fun_time polls this file to
    know what this player is showing.  What goes in it is
    :func:`main_player.status.status_fields`, which is pinned key by key in
    tests/test_main_player_status.py; what is pinned here is the wiring between that and
    the file -- the part a loop-splitting change can quietly drop, leaving the
    whole suite green while the field it stopped filling publishes empty.
    """

    def test_a_status_carries_the_touch_the_trace_had_chosen_by_then(self, tmp_path):
        """Asked of the gate as the status is written, not captured when the
        writer is built: the choice is made while the frame is painted, and the
        writer publishes at its own throttled cadence in between."""
        status = tmp_path / "main_player_status.txt"
        gate = FakeGate()
        writer = _writer(_args(status), gate)

        gate.touch = 4200          # chosen later, while a frame was painted
        writer.write(StubSession())

        assert status.read_text(encoding="utf-8") == "".join(
            f"{key}={value}\n"
            for key, value in status_fields(StubSession(), 4200).items())

    def test_no_touch_chosen_yet_publishes_the_empty_field(self, tmp_path):
        """Zero is a real media time; an arbiter reading one would end Genau's
        turn at the top of the video."""
        status = tmp_path / "main_player_status.txt"
        writer = _writer(_args(status), FakeGate(None))

        writer.write(StubSession())

        assert "handoff_touch_ms=\n" in status.read_text(encoding="utf-8")

def _run_body() -> ast.FunctionDef:
    """`_run`'s syntax tree.

    Read off the source, the way tests/test_session_quit.py and
    test_focus_clickthrough read their own guarantees, and for the same reason:
    the loop needs a real window and the libmpv DLL, so it cannot be run here at
    all.  What that leaves testable is the wiring -- which part is handed what,
    and in which order -- and the wiring is where the parts this module was
    split into can be joined up wrong.
    """
    tree = ast.parse((Path(__file__).resolve().parents[1] / "main_player" / "app.py")
                     .read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_run")


def _call(where: ast.AST, spelling: str) -> ast.Call:
    """The one call written exactly as *spelling* in *where*."""
    calls = [n for n in ast.walk(where)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == spelling]
    assert len(calls) == 1, f"expected one {spelling}(), found {len(calls)}"
    return calls[0]


def _said(node: ast.AST) -> str:
    return ast.unparse(node)


def _run_loop_lines() -> tuple[int, int, int]:
    """The status write, the line a blanked frame skips out at, and the painting."""
    run = _run_body()
    loop = next(n for n in ast.walk(run) if isinstance(n, ast.While))
    skip = next(n.lineno for n in ast.walk(loop) if isinstance(n, ast.Continue))
    return (_call(loop, "status_writer.write").lineno, skip,
            _call(loop, "painter.paint").lineno)


class TestHowTheSevenPartsAreJoinedUp:
    """`_run` assembles the parts this module was split into, and nothing can
    run it: it needs a window and libmpv.  So the joins are read off the source
    instead -- each of these is a mis-wiring that leaves every unit test in the
    suite green, because every unit is correct and only the wiring between them
    is wrong.
    """

    def test_the_status_file_asks_the_gate_the_painting_fills(self):
        """The latch is written in one place only -- the trace, reached through
        the console panel while a frame is painted -- and read in one other, the
        status writer.  Hand those two different gates and the file publishes an
        empty handoff touch for the life of the process, which is the arbiter
        going back to its own read of the wave: the exact split this player's
        trace exists to close."""
        run = _run_body()
        built = [n for n in ast.walk(run)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "DriveGate"]

        assert len(built) == 1, "two gates: one of them is never filled"
        assert _said(_call(run, "_status_writer").args[1]) == "drive_gate"
        assert _said(_call(run, "ConsolePanel").keywords[1].value) == "drive_gate"

    def test_the_painting_is_told_where_the_pointer_is(self):
        """The hover is the one thing on the panel the mouse owns rather than
        the player, so it comes in per frame.  Passed None, every control on the
        HUD stops lighting up under the cursor and nothing else changes."""
        assert _said(_call(_run_body(), "painter.paint").keywords[0].value) == "pointer.hover"

    def test_both_parts_are_given_the_window_the_way_round_it_was_measured(self):
        """One `screen.get_size()` at the top of the frame feeds both, and the
        pair is (width, height) in both.  Transposed, every overlay is laid out
        against a 600x1000 window in a 1000x600 one and every press maps to the
        wrong place."""
        run = _run_body()

        assert [_said(a) for a in _call(run, "painter.paint").args] == ["win_w", "win_h"]
        assert [_said(a) for a in _call(run, "window_input.deal").args[1:]] == ["win_w", "win_h"]

    def test_the_mode_is_written_down_out_of_the_modes_themselves(self):
        """Dropped, the main player writes main_player_mode.txt once at startup and never again: the
        next session opens on this one's playlist while the HUD names a mode
        from before it, and a compilation entered here is lost outright."""
        assert _said(_call(_run_body(), "memory.sync").args[0]) == "modes.remembered"


class TestWhatABlankedFrameStillDoes:
    """Fun Time gives the main slot's rect to Genau in genau mode and blanks
    The main player, and the loop skips everything that builds a picture nobody can see.
    WHICH side of that skip each step is on is the whole of the rule, and it is
    load-bearing in both directions.
    """

    def test_the_status_goes_out_before_the_frame_is_skipped(self):
        """A blanked main player is still playing: clipper_save reads its playhead, the
        dashboard reads its funscript flags, and the loop range lives nowhere
        else at all.  Below the skip, the file freezes for as long as Genau has
        the slot -- including the handoff touch, which is the field this
        player's whole trace exists to publish."""
        write, skip, _paint = _run_loop_lines()

        assert write < skip

    def test_the_painting_is_what_the_skip_is_for(self):
        """The other side of it: five overlays and a heatmap rebuild, sixty
        times a second, on top of a video nobody can see."""
        _write, skip, paint = _run_loop_lines()

        assert skip < paint


class TestWhenSomethingCosmeticFails:
    """Two things the main player does on the way in are decoration -- its window icon and
    the taskbar button it claims -- and both catch everything, because neither
    may cost a launch.  Caught silently, though, a permanently broken one is
    indistinguishable in the log from one that works.
    """

    def test_an_icon_it_cannot_read_is_said_rather_than_swallowed(
        self, tmp_path, caplog, monkeypatch,
    ):
        from main_player import app
        not_an_icon = tmp_path / "icon.ico"
        not_an_icon.write_text("this is not an icon", encoding="utf-8")

        with caplog.at_level(logging.DEBUG, logger="main_player.app"):
            surface = app._load_icon_surface(not_an_icon)

        assert surface is None
        assert "icon" in caplog.records[0].getMessage().lower()


class TestWhichConfigTheFlagsAreReadAgainst:
    """The main player parses twice on purpose.  The first pass exists only to find out
    whether ``--config`` names a file; if it does, the parser is built again
    from THAT file, because every default it feeds -- the library directories,
    the state dir, the device's port -- comes from it.
    """

    def _main(self, monkeypatch, argv):
        from main_player import app
        landed = []
        monkeypatch.setattr(app, "_run", lambda args: landed.append(args) or 0)
        app.main(argv)
        return landed[0]

    def test_a_named_config_supplies_the_defaults(self, tmp_path, monkeypatch):
        config = tmp_path / "genau_config.json"
        config.write_text('{"main_player": {"tcode_udp_port": 51000}}', encoding="utf-8")

        args = self._main(monkeypatch, ["--config", str(config)])

        assert args.tcode_port == 51000

    def test_a_flag_still_beats_the_config_it_named(self, tmp_path, monkeypatch):
        """The whole line is parsed again, not patched, so the flags keep
        winning -- which they would not if the second pass only filled gaps."""
        config = tmp_path / "genau_config.json"
        config.write_text('{"main_player": {"tcode_udp_port": 51000}}', encoding="utf-8")

        args = self._main(
            monkeypatch, ["--config", str(config), "--tcode-port", "50999"])

        assert args.tcode_port == 50999


class Spy:
    """Every method asked of it is recorded under its own name, so a keyword
    wired to the wrong collaborator shows up as the wrong label."""

    def __init__(self, label: str, log: list) -> None:
        self._label = label
        self._log = log

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self._log.append((self._label, name, args, kwargs))
        return record


class TestWhichCollaboratorEachVerbReaches:
    """Eight collaborators are handed to the dispatcher as one record, and two
    of them swapped would be invisible: every verb would still be answered, and
    the suite would stay green while PLAY_FULL_VID played a clip jump.  The
    dispatcher's own tests pin the keyword-to-behavior half; this pins the
    wiring-to-keyword half, which is the half nothing had.
    """

    VERBS = [
        ("RELOAD_PLAYLIST", "take_up_playlist", "__call__"),
        ("TOGGLE_LENGTH_MODE", "modes", "toggle_length"),
        ("SET_LENGTH_MODE shorts", "modes", "set_length"),
        ("END_COMPILATION", "modes", "end_compilation"),
        ("SET_F_MODE 1", "modes", "set_f_mode"),
        ("PLAY_COMPILATION", "jumps", "play_compilation"),
        ("PLAY_FULL_VID", "jumps", "play_full_vid"),
        ("PLAY_CLIP_JUMP", "jumps", "play_clip_jump"),
        ("JUMP_TO_FUNSCRIPT", "funscript_jumps", "jump_to_funscript"),
        ("NEXT_FUNSCRIPTED", "funscript_jumps", "next_funscripted"),
        ("SET_VOLUME 40", "volume", "set"),
        ("DISPLAY_ON", "display", "set_active"),
    ]

    @staticmethod
    def _wiring(log, stop_event=None):
        from main_player.app import _controls
        return _controls(
            Spy("session", log), stop_event or threading.Event(),
            modes=Spy("modes", log), jumps=Spy("jumps", log),
            funscript_jumps=Spy("funscript_jumps", log), volume=Spy("volume", log),
            display=Spy("display", log),
            take_up_playlist=lambda: log.append(("take_up_playlist", "__call__", (), {})),
        )

    @pytest.mark.parametrize("command, who, what", VERBS)
    def test_it_reaches_that_one_and_no_other(self, command, who, what):
        log: list = []

        apply_command(command, self._wiring(log))

        assert [(label, name) for label, name, *_ in log
                if label != "session"] == [(who, what)]

    def test_quit_sets_the_stop_event_rather_than_asking_anyone(self):
        """The only verb that ends the loop itself; everything else in a
        session goes through the dashboard."""
        log: list = []
        stop_event = threading.Event()

        apply_command("QUIT", self._wiring(log, stop_event))

        assert stop_event.is_set()
