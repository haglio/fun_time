from __future__ import annotations

import configparser
import logging
from pathlib import Path
import contextlib
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from fun_time.config import load_config
from fun_time.player_status import (
    genau_status_path,
    read_nau_status,
)
from fun_time.loading_screen import STALE_TIMEOUT_S
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    LaunchManifest,
    RandomFavsBrowserSettings,
    write_windows_bridge_manifest,
)
from fun_time.nau_console import nau_console_path
from fun_time import windows_bridge_sequencer
from fun_time.windows_bridge_random_favs_browser import ChromeShortcut
from fun_time.windows_bridge_sequencer import (
    release_the_players,
    _wait_for_players_drawing,
    NAU_LOAD_TIMEOUT_S,
    WINDOW_RESOLVE_TIMEOUT_S,
    run_startup_sequence,
    _maybe_launch_random_favs_browser,
    _resolve_satellite_hwnds,
    _wait_for_nau_loaded,
)
from fun_time.monitors import MonitorInfo
from fun_time.window_layout import (
    MonitorRect,
    WindowLayoutPlan,
)
from fun_time.config import LayoutConfig
from fun_time.overlay_progress import STARTUP_PHASES, NullProgress, StartupCancelled

import pytest


FAKE_MONITORS = [
    MonitorInfo(x=0, y=0, width=2560, height=1392),
    MonitorInfo(x=2560, y=0, width=1440, height=3440),
]

CORE_PIDS = {"portrait_pid": 30, "landscape_pid": 40}
UI_PIDS = {"dashboard_pid": 50, "audio_pid": 70}
GENAU_PID = 60
NAU_PID = 25

# Main slot on the secondary monitor with conftest's main_top_ratio=0.727:
# portrait height = int(3440 * 0.727) = 2500, main player height = 940.
PRIMARY_MEDIA_RECT = {"x": 2560, "y": 2500, "width": 1440, "height": 940}


def _make_manifest(cfg_factory, tmp_path):
    cfg = load_config(cfg_factory())
    manifest_path = write_windows_bridge_manifest(
        cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )
    return cfg, manifest_path


def _pause_every_player(m) -> list[Path]:
    """The three paused flags, all set — the state seed_startup_states leaves."""
    flags = [Path(m.commands.nau_paused_file), Path(m.commands.genau_paused_file),
             Path(m.commands.audio_paused_file)]
    for flag in flags:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1", encoding="utf-8")
    return flags


def _write_result(result_file, values):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {k: str(v) for k, v in values.items()}
    path = Path(result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def _fake_core(**kwargs):
    _write_result(kwargs["result_file"], CORE_PIDS)
    return "nau"


def _fake_core_in(mode: str):
    """A core session that resumes into *mode* — what its real one returns."""
    def launch(**kwargs):
        _write_result(kwargs["result_file"], CORE_PIDS)
        return mode
    return launch


def _seed_paused_flags(manifest_path) -> dict[str, Path]:
    """Hold all three main-slot flags, as the core session's seeding does."""
    m = configparser.ConfigParser()
    m.optionxform = str
    m.read(str(manifest_path), encoding="utf-8")
    flags = {}
    for key in ("nau_paused_file", "genau_paused_file", "audio_paused_file"):
        path = Path(m["commands"][key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
        flags[key] = path
    return flags


def _run_revealing_sequence(manifest_path, tmp_path, mode: str) -> None:
    """Startup through to the reveal, resuming into *mode*, windows all faked."""
    with _sequencer_stubs(
            start_core_session=dict(side_effect=_fake_core_in(mode)),
            wait_for_window_by_title=dict(return_value=88888)):
        run_startup_sequence(
            manifest_path=manifest_path, state_dir=tmp_path, hide_windows=False,
        )


@contextlib.contextmanager
def _sequencer_stubs(**overrides):
    """The startup sequence's collaborators, stubbed at their boundary, once.

    The same ten-name patch stack used to be spelled out in 26 tests; a test
    that needs a collaborator to behave differently passes the mock kwargs for
    it (``wait_for_window_by_title=dict(side_effect=...)``), and reads any
    mock back off the yielded namespace.  The clock is deliberately NARROW —
    ``monotonic`` frozen at zero and a no-op ``sleep``, not a module-wide
    MagicMock — so a new ``time.*`` use in the sequencer fails loudly here
    instead of silently answering with a fresh mock.
    """
    spec: dict[str, dict] = {
        "start_core_session": dict(side_effect=_fake_core),
        "launch_genau": dict(return_value=GENAU_PID),
        "launch_nau": dict(side_effect=_fake_nau),
        "launch_ui_companions": dict(side_effect=_fake_ui),
        "enumerate_monitors": dict(return_value=FAKE_MONITORS),
        "wait_for_window_by_title": dict(return_value=99999),
        "move_window": {},
        "set_always_on_top": {},
        "minimize_window": {},
        "disable_window_transitions": {},
    }
    spec.update(overrides)
    with contextlib.ExitStack() as stack:
        mocks = SimpleNamespace()
        for name, kwargs in spec.items():
            setattr(mocks, name, stack.enter_context(
                patch(f"fun_time.windows_bridge_sequencer.{name}", **kwargs)))
        stack.enter_context(patch(
            "fun_time.windows_bridge_sequencer.time",
            SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _s: None)))
        yield mocks



def _fake_ui(**kwargs):
    _write_result(kwargs["result_file"], UI_PIDS)


def _fake_nau(**kwargs):
    """Nau, launched: its status file appears once it has a video up.

    The overlay is held on exactly that file, so a fake that returned a pid and
    wrote nothing would leave every startup here waiting out the full budget.
    """
    Path(kwargs["status_file"]).write_text("video=this_session.mp4\n", encoding="utf-8")
    return NAU_PID


def test_phase_result_files_are_unique_per_launch(tmp_path, monkeypatch):
    """Each phase's result INI carries the launch's clock stamp, so a second
    launch into the same state dir writes beside the first's results instead
    of clobbering them.  Two clock readings, two paths — under the suite's
    old frozen-clock stubs every call returned <prefix>_0.ini, a version of
    the helper with no uniqueness at all."""
    ticks = iter([12.001, 47.002])
    monkeypatch.setattr(windows_bridge_sequencer, "time",
                        SimpleNamespace(monotonic=lambda: next(ticks)))

    first = windows_bridge_sequencer._build_unique_result_path(tmp_path, "phase1")
    second = windows_bridge_sequencer._build_unique_result_path(tmp_path, "phase1")

    assert first != second
    assert first.parent == tmp_path and first.name.startswith("phase1_")


class TestRunStartupSequence:
    def _captured_launch(self, cfg_factory, tmp_path):
        """One startup run with the core and UI launch kwargs captured."""
        cfg = load_config(cfg_factory({
            "regen": {
                "media_root": str(tmp_path / "media"),
                "metadata_root": str(tmp_path / "metadata"),
            }
        }))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        core_called = {}
        ui_called = {}

        def capture_core(**kwargs):
            core_called.update(kwargs)
            _write_result(kwargs["result_file"], CORE_PIDS)

        def capture_ui(**kwargs):
            ui_called.update(kwargs)
            _write_result(kwargs["result_file"], UI_PIDS)

        with _sequencer_stubs(start_core_session=dict(side_effect=capture_core),
                              launch_ui_companions=dict(side_effect=capture_ui)):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)
        return cfg, result, core_called, ui_called

    def test_every_phases_pids_are_gathered_into_the_launch_result(self, cfg_factory, tmp_path):
        _cfg, result, _core, _ui = self._captured_launch(cfg_factory, tmp_path)

        assert result.nau_pid == NAU_PID
        assert result.portrait_pid == 30
        assert result.landscape_pid == 40
        assert result.dashboard_pid == 50
        assert result.genau_pid == GENAU_PID
        assert result.audio_pid == 70

    def test_the_satellites_are_wired_from_the_manifest(self, cfg_factory, tmp_path):
        """OUR python (the satellite player ships from this repo, so it must
        not depend on genau's venv), the satellite module, and each side's
        files — plus a log per windowed player: under pythonw there is no
        console, so without one an unhandled exception dies tracelessly."""
        cfg, _result, core_called, _ui = self._captured_launch(cfg_factory, tmp_path)

        assert core_called["satellite_python_exe"] == str(cfg.paths.python_exe)
        assert core_called["satellite_python_exe"] != str(cfg.paths.genau_python_exe)
        assert core_called["satellite_module"] == "satellite"
        state = cfg.paths.state_dir
        for side in ("portrait", "landscape"):
            slot = core_called[side]
            assert slot.side.label == side
            assert slot.cmd_file == str(state / f"{side}_cmd.txt")
            assert slot.paused_file == str(state / f"{side}_paused.txt")
            assert slot.status_file == str(state / f"{side}_status.txt")
            assert slot.log_file == tmp_path / f"{side}_satellite.log"
            assert slot.playlist_file == tmp_path / f"{side}_playlist.tsv"
        # Nau's status file rides along too: startup resumes each player onto
        # the video its status file names, and Nau is the third of the three.
        assert core_called["nau_status_file"] == str(cfg.nau_status_file)
        assert core_called["nau_paused_file"] == str(cfg.nau_paused_file)

    def test_the_satellites_launch_straight_into_their_layout_rects(self, cfg_factory, tmp_path):
        """mpv won't rescale on a later Win32 resize, so the sequencer threads
        the computed rects into the core launch — this is what makes the
        native video fill its window."""
        _cfg, _result, core_called, ui_called = self._captured_launch(cfg_factory, tmp_path)

        assert core_called["portrait"].rect.x == 2560
        assert core_called["portrait"].rect.width > 0
        assert core_called["landscape"].rect != core_called["portrait"].rect
        # The same plan reaches the dashboard, which is launched separately.
        assert ui_called["dashboard_width"] > 0

    def test_the_core_launch_carries_the_session_files_through(self, cfg_factory, tmp_path):
        """The favs list and state dir, the provider roots (so the startup
        build can collapse action groups), and the broker's heartbeat and
        command paths (so startup can leave a live broker running, and park
        the OSR2 for the long wait)."""
        cfg, _result, core_called, _ui = self._captured_launch(cfg_factory, tmp_path)

        assert core_called["favs_file"] == str(cfg.paths.favs_file)
        assert core_called["state_dir"] == tmp_path
        assert core_called["regen_metadata_root"] == tmp_path / "metadata"
        assert core_called["broker_heartbeat_file"] == str(cfg.broker_heartbeat_file)
        assert core_called["broker_cmd_file"] == str(cfg.broker_cmd_file)

    def test_the_ui_launch_enables_the_dashboard_and_forwards_the_rfb_rect(self, cfg_factory, tmp_path):
        """The RFB rect flows through so the reference popup can fill that
        space."""
        _cfg, _result, _core, ui_called = self._captured_launch(cfg_factory, tmp_path)

        assert ui_called["dashboard_enabled"] is True
        assert {"rfb_x", "rfb_y", "rfb_width", "rfb_height"} <= set(ui_called)
        assert all(
            isinstance(ui_called[key], int)
            for key in ("rfb_x", "rfb_y", "rfb_width", "rfb_height")
        )

    def test_launches_genau_and_nau_with_primary_media_rect(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        genau_kwargs = {}
        nau_kwargs = {}

        def capture_genau(**kwargs):
            genau_kwargs.update(kwargs)
            return GENAU_PID

        def capture_nau(**kwargs):
            nau_kwargs.update(kwargs)
            return _fake_nau(**kwargs)

        with _sequencer_stubs(launch_genau=dict(side_effect=capture_genau), launch_nau=dict(side_effect=capture_nau), wait_for_window_by_title=dict(return_value=88888)):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # Genau receives its manifest file paths and the shared main-slot rect.
        assert genau_kwargs["command_file"] == str(cfg.genau_cmd_file)
        assert genau_kwargs["paused_file"] == str(cfg.genau_paused_file)
        assert genau_kwargs["clips_folder"] == str(cfg.paths.clips_dir)
        # The drive readout is a channel between the two of them, so both are told
        # the same path.  Each resolving it for itself is how Hybrid ended up with
        # no readout at all: Genau wrote it beside its own config, Nau read ours.
        assert genau_kwargs["drive_file"] == nau_kwargs["drive_file"]
        assert {key: genau_kwargs[key] for key in ("genau_x", "genau_y", "genau_width", "genau_height")} == {
            f"genau_{axis}": value for axis, value in zip(("x", "y", "width", "height"), PRIMARY_MEDIA_RECT.values())
        }

        # Nau is wired from the manifest [modules]/[commands] keys and the same rect.
        assert nau_kwargs == {
            "python_exe": str(cfg.paths.python_exe),
            "nau_module": "nau",
            "config_path": str(cfg.config_path),
            "playlist_file": str(cfg.nau_playlist_file),
            "command_file": str(cfg.nau_cmd_file),
            "paused_file": str(cfg.nau_paused_file),
            "status_file": str(cfg.nau_status_file),
            # The console: the panel Fun Time publishes for Nau's HUD, Genau's
            # readout for the section under it, and where a press goes back.
            "console_file": str(nau_console_path(cfg.paths.state_dir)),
            "drive_file": Path(cfg.genau_cmd_file).parent / "genau_drive.txt",
            # Nau is the satellites' twin and gets the same crash log.
            "log_file": tmp_path / "nau.log",
            "nau_x": PRIMARY_MEDIA_RECT["x"],
            "nau_y": PRIMARY_MEDIA_RECT["y"],
            "nau_width": PRIMARY_MEDIA_RECT["width"],
            "nau_height": PRIMARY_MEDIA_RECT["height"],
            # This manifest has no regen.metadata_root, so Nau is left to
            # group by name; launch_nau's --metadata-dir wiring is covered in
            # test_windows_bridge_startup.
            "metadata_dir": None,
            # Where a press on Nau's volume control posts its command — the same
            # file the dashboard and each satellite's HUD write to.
            "dashboard_cmd_file": str(cfg.paths.state_dir / "dashboard_cmd.txt"),
            # Which checkouts of ../genau and ../player_core to run — empty in
            # an ordinary session, so both players resolve through their venv.
            "project_dirs": "",
        }

    def test_both_players_are_run_out_of_the_named_checkouts(self, cfg_factory, tmp_path):
        """Genau and Nau both ship in that repo, so a branch of it has to move
        the pair — one on the branch and one on the primary is two different
        codebases sharing a console."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        checkout = tmp_path / "genau_worktree"
        checkout.mkdir()
        manifest = configparser.ConfigParser()
        manifest.optionxform = str
        manifest.read(manifest_path, encoding="utf-8")
        manifest["runtime"]["genau_project_dirs"] = str(checkout)
        with Path(manifest_path).open("w", encoding="utf-8") as fp:
            manifest.write(fp)
        genau_kwargs: dict = {}
        nau_kwargs: dict = {}

        def capture_genau(**kwargs):
            genau_kwargs.update(kwargs)
            return GENAU_PID

        def capture_nau(**kwargs):
            nau_kwargs.update(kwargs)
            return _fake_nau(**kwargs)

        with _sequencer_stubs(launch_genau=dict(side_effect=capture_genau), launch_nau=dict(side_effect=capture_nau), wait_for_window_by_title=dict(return_value=88888)):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert genau_kwargs["project_dirs"] == str(checkout)
        assert nau_kwargs["project_dirs"] == str(checkout)

    def test_positions_satellite_windows_and_applies_topmost_policy(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        title_to_hwnd = {
            "Genau": 6060,
            "Nau": 2525,
            "Portrait AI Player": 3030,
            "Landscape AI Player": 4040,
        }
        move_calls: list[tuple] = []
        topmost_calls: list[tuple] = []

        with _sequencer_stubs(wait_for_window_by_title=dict(side_effect=lambda title, **kw: title_to_hwnd.get(title, 0)), move_window=dict(side_effect=lambda hwnd, x, y, w, h, **_kw: move_calls.append((hwnd, x, y, w, h))), set_always_on_top=dict(side_effect=lambda h, v: topmost_calls.append((h, v)))):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # The two satellite windows are positioned immediately in normal mode.
        moved_hwnds = {c[0] for c in move_calls}
        assert {3030, 4040} <= moved_hwnds

        # nau startup mode: the windows that own a rect are promoted to topmost,
        # Nau (2525) included so it floats above the desktop like the main player
        # player always has.  Genau (6060) is the hidden slot-mate and stays out
        # of the band — it is promoted last, so being in it puts it over Nau.
        promoted = {h for h, on in topmost_calls if on}
        assert promoted == {3030, 4040, 2525}

    def test_non_hidden_path_unpauses_nau(self, cfg_factory, tmp_path):
        """The no-loading-screen path (integration / normal without the
        overlay) must still start Nau — the reveal that clears nau_paused
        cannot live only in the hidden branch."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = configparser.ConfigParser()
        m.optionxform = str
        m.read(str(manifest_path), encoding="utf-8")
        nau_paused = Path(m["commands"]["nau_paused_file"])
        nau_paused.parent.mkdir(parents=True, exist_ok=True)
        nau_paused.write_text("1", encoding="utf-8")  # seeded paused at startup

        with _sequencer_stubs(wait_for_window_by_title=dict(return_value=88888)):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path, hide_windows=False)

        assert nau_paused.read_text(encoding="utf-8").strip() == "0"

    def test_genau_is_launched_onto_the_clip_it_was_left_showing(self, cfg_factory, tmp_path):
        """Genau's status file is the only record of which clip was up — it
        rescans its folder every launch — so it is read before Genau is started
        and handed straight back on the command line."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        genau_status_path(cfg.paths.state_dir).parent.mkdir(parents=True, exist_ok=True)
        genau_status_path(cfg.paths.state_dir).write_text(
            "cruise=0\nclip=C:\\clips\\alpha.mp4\n", encoding="utf-8",
        )

        with _sequencer_stubs(wait_for_window_by_title=dict(return_value=88888)) as stubs:
            launch = stubs.launch_genau

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert launch.call_args.kwargs["start_clip"] == "C:\\clips\\alpha.mp4"

    def test_a_genau_session_parks_nau_and_gives_genau_the_slot(self, cfg_factory, tmp_path):
        """Reopening in genau mode: the session is still BUILT in nau — Nau loads
        the main player's playlist and the overlay waits on it — but what is revealed
        is Genau, so the pair swaps which one is parked and which one floats."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        title_to_hwnd = {
            "Genau": 6060, "Nau": 2525,
            "Portrait AI Player": 3030, "Landscape AI Player": 4040,
        }
        topmost_calls: list[tuple] = []
        minimized: list[int] = []

        with _sequencer_stubs(start_core_session=dict(side_effect=_fake_core_in("genau")), wait_for_window_by_title=dict(side_effect=lambda title, **kw: title_to_hwnd.get(title, 0)), set_always_on_top=dict(side_effect=lambda h, v: topmost_calls.append((h, v))), minimize_window=dict(side_effect=lambda h, **_kw: minimized.append(h))):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert minimized == [2525]
        assert {h for h, on in topmost_calls if on} == {3030, 4040, 6060}
        # Handed on, because the post-overlay z-order pass has to re-assert the
        # same policy and it runs from the orchestrator, out of reach of this.
        assert result.main_mode == "genau"

    def test_a_genau_session_is_revealed_by_starting_genau_not_nau(self, cfg_factory, tmp_path):
        """The reveal starts whichever player owns the display, and only that
        one: unpausing Nau regardless would put a video up behind the parked
        window and hand the OSR2 two drivers at once."""
        paused = _seed_paused_flags(_make_manifest(cfg_factory, tmp_path)[1])

        _run_revealing_sequence(_make_manifest(cfg_factory, tmp_path)[1], tmp_path, "genau")

        assert paused["nau_paused_file"].read_text(encoding="utf-8").strip() == "1"
        assert paused["genau_paused_file"].read_text(encoding="utf-8").strip() == "0"
        assert paused["audio_paused_file"].read_text(encoding="utf-8").strip() == "0"

    def test_a_hybrid_session_is_revealed_by_starting_both(self, cfg_factory, tmp_path):
        """Hybrid runs both: Nau's video with Genau driving the OSR2 over it."""
        paused = _seed_paused_flags(_make_manifest(cfg_factory, tmp_path)[1])

        _run_revealing_sequence(_make_manifest(cfg_factory, tmp_path)[1], tmp_path, "hybrid")

        assert paused["nau_paused_file"].read_text(encoding="utf-8").strip() == "0"
        assert paused["genau_paused_file"].read_text(encoding="utf-8").strip() == "0"

    def test_a_genau_session_hands_genau_the_osr2_at_the_reveal(self, cfg_factory, tmp_path):
        """Genau's stroke rides its command channel, not the paused flag, so the
        mode where it drives outright is started here — and only here, because
        startup held it on that channel so it could not drive the device while the
        loading screen was still up."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        _seed_paused_flags(manifest_path)
        genau_cmd = Path(cfg.genau_cmd_file)
        genau_cmd.parent.mkdir(parents=True, exist_ok=True)
        genau_cmd.write_text("", encoding="utf-8")

        _run_revealing_sequence(manifest_path, tmp_path, "genau")

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == ["RESUME"]

    def test_a_hybrid_session_leaves_genau_to_the_arbiter_at_the_reveal(
        self, cfg_factory, tmp_path,
    ):
        """No RESUME in hybrid: the dispatch loop's arbiter picks between Genau
        and the funscript on its first tick, and starting Genau here would put it
        on the device against a funscript about to take it."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        _seed_paused_flags(manifest_path)
        genau_cmd = Path(cfg.genau_cmd_file)
        genau_cmd.parent.mkdir(parents=True, exist_ok=True)
        genau_cmd.write_text("", encoding="utf-8")

        _run_revealing_sequence(manifest_path, tmp_path, "hybrid")

        assert genau_cmd.read_text(encoding="utf-8") == ""

    def test_a_nau_session_leaves_genau_parked_at_the_reveal(self, cfg_factory, tmp_path):
        """Nau's own mode: Genau stays held, so nothing drives the OSR2 behind
        the minimized window."""
        paused = _seed_paused_flags(_make_manifest(cfg_factory, tmp_path)[1])

        _run_revealing_sequence(_make_manifest(cfg_factory, tmp_path)[1], tmp_path, "nau")

        assert paused["nau_paused_file"].read_text(encoding="utf-8").strip() == "0"
        assert paused["genau_paused_file"].read_text(encoding="utf-8").strip() == "1"
        assert paused["audio_paused_file"].read_text(encoding="utf-8").strip() == "1"

    def test_hybrid_stacks_genau_over_nau_and_parks_neither(self, cfg_factory, tmp_path):
        """Hybrid is the one mode where both share the rect: Genau's transparent
        HUD sits over Nau's video, which the topmost band expresses as promoting
        Nau first and Genau last."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        title_to_hwnd = {
            "Genau": 6060, "Nau": 2525,
            "Portrait AI Player": 3030, "Landscape AI Player": 4040,
        }
        topmost_calls: list[tuple] = []
        minimized: list[int] = []

        with _sequencer_stubs(start_core_session=dict(side_effect=_fake_core_in("hybrid")), wait_for_window_by_title=dict(side_effect=lambda title, **kw: title_to_hwnd.get(title, 0)), set_always_on_top=dict(side_effect=lambda h, v: topmost_calls.append((h, v))), minimize_window=dict(side_effect=lambda h, **_kw: minimized.append(h))):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert minimized == []
        promoted = [h for h, on in topmost_calls if on]
        assert set(promoted) == {3030, 4040, 2525, 6060}
        assert promoted.index(6060) > promoted.index(2525)


class _TrackingProgress:
    """A ProgressReporter that records the phases it is told, in order.

    Carries ``cancelled`` because the real reporter does and the sequencer reads
    it — the wait for Nau polls it, so a double without it fails there rather
    than at the assertion.
    """

    cancelled = False

    def __init__(self, log: list[str] | None = None) -> None:
        self.phases: list[str] = log if log is not None else []

    def advance(self, phase: str) -> None:
        self.phases.append(phase)

    def finish(self) -> None:
        pass


class _CancelOnAdvance:
    """A ProgressReporter that cancels on its Nth ``advance`` call.

    Stands in for the real StartupProgress once the loading screen has dropped
    the cancel flag: the Nth checkpoint raises instead of writing progress.
    """

    def __init__(self, cancel_on: int) -> None:
        self._cancel_on = cancel_on
        self.calls = 0

    @property
    def cancelled(self) -> bool:
        return self.calls >= self._cancel_on

    def advance(self, message: str) -> None:
        self.calls += 1
        if self.calls >= self._cancel_on:
            raise StartupCancelled()

    def finish(self) -> None:
        pass


class TestTheOrderInsideTheStartupPhases:
    """The sequence is the design, not an accident of how it was written.

    Every one of these was a paragraph of comment inside the one 91-statement
    function; each is now an assertion, so a phase moved past another fails
    here instead of at the next session.
    """

    def _sequence(self, cfg_factory, tmp_path, *, hide_windows=False,
                  overrides=None, extra_stubs=None) -> list[str]:
        """One startup run, reduced to the order its collaborators were called."""
        cfg = load_config(cfg_factory(overrides or {}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
        order: list[str] = []

        def note(name, then=None):
            def recorder(*args, **kwargs):
                order.append(name)
                return then(**kwargs) if then is not None else 0
            return recorder

        stubs = dict(
            enumerate_monitors=dict(side_effect=note("layout", lambda **k: FAKE_MONITORS)),
            start_core_session=dict(side_effect=note("core", _fake_core)),
            launch_genau=dict(side_effect=note("genau", lambda **k: GENAU_PID)),
            launch_nau=dict(side_effect=note("nau", _fake_nau)),
            launch_ui_companions=dict(side_effect=note("companions", _fake_ui)),
        )
        stubs.update(extra_stubs or {})
        with _sequencer_stubs(**stubs), \
             patch("fun_time.windows_bridge_sequencer._maybe_launch_random_favs_browser",
                   side_effect=note("browser")), \
             patch("fun_time.windows_bridge_sequencer.write_flag_file",
                   side_effect=note("release")):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path,
                                 hide_windows=hide_windows)
        return order

    def test_the_layout_is_computed_before_any_player_launches(self, cfg_factory, tmp_path):
        """mpv sizes its output to the geometry it was launched with and will
        NOT rescale when a later Win32 move resizes the window, so a satellite
        has to be started straight into its real rect.  Computing the layout
        after the launch would hand it the wrong one."""
        order = self._sequence(cfg_factory, tmp_path)

        assert order.index("layout") < order.index("core")

    def test_the_media_stack_is_up_before_the_ui_companions(self, cfg_factory, tmp_path):
        """Genau and Nau are launched as early as possible so they can init
        pygame, scan media and decode first frames while the rest of startup
        continues — which is only worth anything if the rest still follows."""
        order = self._sequence(cfg_factory, tmp_path)

        assert order.index("genau") < order.index("companions")
        assert order.index("nau") < order.index("companions")

    def test_the_browser_is_up_before_the_dashboard_that_opens_over_it(
            self, cfg_factory, tmp_path):
        """The dashboard's reference popup opens over the browser's rect."""
        order = self._sequence(cfg_factory, tmp_path)

        assert order.index("browser") < order.index("companions")

    def test_the_players_are_released_only_once_the_room_is_built(
            self, cfg_factory, tmp_path):
        """Startup holds every player so nothing plays into a room that is
        still being built; the release is therefore the last thing the phases
        do on the path with no cover to hide behind."""
        order = self._sequence(cfg_factory, tmp_path)

        assert order[-1] == "release"
        assert order.index("companions") < order.index("release")

    def test_a_covered_startup_releases_nothing_at_all(self, cfg_factory, tmp_path):
        """With a loading screen the release belongs to the orchestrator, once
        the cover is off the screen: released here, a video (and Genau's audio)
        runs behind the cover and its first seconds are gone unseen."""
        order = self._sequence(cfg_factory, tmp_path, hide_windows=True,
                               extra_stubs=dict(restore_window=dict()))

        assert "release" not in order


class TestRunStartupSequenceCancellation:
    def test_cancel_before_companions_reports_only_the_core_children(self, cfg_factory, tmp_path):
        """Cancelling at the layout checkpoint (2nd advance) has launched the
        core stack — the satellites, Genau and Nau — but not the companions."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        ui = MagicMock()

        with _sequencer_stubs(launch_ui_companions=dict(ui)):
            with pytest.raises(StartupCancelled) as excinfo:
                run_startup_sequence(
                    manifest_path=manifest_path, state_dir=tmp_path,
                    progress=_CancelOnAdvance(cancel_on=2), hide_windows=True,
                )

        exc = excinfo.value
        assert set(exc.launched_pids) == {30, 40, GENAU_PID, NAU_PID}
        assert exc.rfb_hwnd == 0
        ui.assert_not_called()

    def test_cancel_after_companions_reports_every_child_and_the_browser(self, cfg_factory, tmp_path):
        """Cancelling once companions are up reports the whole tree — satellites,
        Genau, Nau, dashboard, audio — plus the Random Favs Browser hwnd."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with _sequencer_stubs(_maybe_launch_random_favs_browser=dict(return_value=7777), wait_for_window_by_title=dict(return_value=88888)):
            with pytest.raises(StartupCancelled) as excinfo:
                run_startup_sequence(
                    manifest_path=manifest_path, state_dir=tmp_path,
                    progress=_CancelOnAdvance(cancel_on=5), hide_windows=True,
                )

        exc = excinfo.value
        assert set(exc.launched_pids) == {30, 40, GENAU_PID, NAU_PID, 50, 70}
        assert exc.rfb_hwnd == 7777


class TestNoActivateWindowDuringIntegration:
    """During integration tests, window moves must not steal focus."""

    def test_moves_windows_without_activation_in_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        move_activates: list[bool] = []

        with _sequencer_stubs(wait_for_window_by_title=dict(return_value=88888), move_window=dict(side_effect=lambda *a, **kw: move_activates.append(kw.get("activate", True)))):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert move_activates, "Windows should still be positioned in integration mode"
        assert all(activate is False for activate in move_activates), \
            f"move_window must not activate during integration: {move_activates}"

    def test_activates_windows_outside_integration_mode(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        move_activates: list[bool] = []

        with _sequencer_stubs(wait_for_window_by_title=dict(return_value=88888), move_window=dict(side_effect=lambda *a, **kw: move_activates.append(kw.get("activate", True)))):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert any(activate is True for activate in move_activates), \
            "move_window should activate core windows in normal mode"


class TestProgressReporting:
    """run_startup_sequence reports progress via the callback."""

    def test_hide_windows_reports_every_phase_in_the_table_in_order(self, cfg_factory, tmp_path):
        """The loading-screen path fires exactly the phases the bar is built from.

        The bar is weighted by these phases and closes when the last one lands on
        the total, so a phase fired out of order — or one skipped, or one the
        table has never heard of — either stalls the bar short of the end or
        closes the overlay early.
        """
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        progress = _TrackingProgress()

        with _sequencer_stubs(wait_for_window_by_title=dict(return_value=88888)):
            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=progress,
                hide_windows=True,
            )

        assert progress.phases == [phase.key for phase in STARTUP_PHASES]

    def test_null_progress_accepted_silently(self, cfg_factory, tmp_path):
        """NullProgress should work as a no-op."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with _sequencer_stubs(wait_for_window_by_title=dict(return_value=88888)):
            result = run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=NullProgress(),
            )

        assert result.nau_pid == NAU_PID


class TestLoadingScreenStartup:
    """When hide_windows=True, positioning is deferred until after UI companions launch."""

    def test_defers_positioning_behind_the_overlay(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        title_to_hwnd = {"Portrait AI Player": 3030, "Landscape AI Player": 4040}
        move_calls: list[tuple] = []
        move_activates: list[bool] = []

        def track_move(hwnd, x, y, w, h, **kw):
            move_calls.append((hwnd, x, y, w, h))
            move_activates.append(kw.get("activate", True))

        with _sequencer_stubs(wait_for_window_by_title=dict(side_effect=lambda title, **kw: title_to_hwnd.get(title, 88888)), move_window=dict(side_effect=track_move)):
            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # The two satellite windows are positioned at final locations during Phase 4
        positioned_hwnds = {hwnd for hwnd, x, y, w, h in move_calls}
        assert {3030, 4040} <= positioned_hwnds

        # Nothing may be activated while the loading screen is up
        assert all(activate is False for activate in move_activates), \
            f"move_window must not activate in loading screen mode: {move_activates}"

    def test_resolves_every_window_by_title_never_by_a_launcher_pid(self, cfg_factory, tmp_path):
        """No window lookup may poll on a pid this sequence launched.

        Every child starts through a venv ``Scripts\\pythonw.exe``, a launcher that
        spawns the base interpreter as a CHILD — and the child owns the window.  So
        the launched pid never matches, and each poll on one runs its full timeout
        before the title lookup that was going to answer anyway.  The two
        satellites and Nau together were 25 seconds of a 28-second loading screen.
        """
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        title_to_hwnd = {
            "Portrait AI Player": 3030,
            "Landscape AI Player": 4040,
            "Nau": 2525,
            "Genau": 6060,
            "Fun Time": 5050,
        }

        with _sequencer_stubs(wait_for_window_by_title=dict(side_effect=lambda title, **kw: title_to_hwnd.get(title, 0))):
            result = run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # Not merely unused here — a pid lookup is not reachable from this module
        # at all, so re-introducing one is a deliberate act rather than a habit.
        assert not hasattr(windows_bridge_sequencer, "wait_for_window")
        assert not hasattr(windows_bridge_sequencer, "find_window_by_pid")
        # And every managed window is still resolved, by caption alone.
        assert result.role_hwnds == {
            "portrait": 3030, "landscape": 4040, "nau": 2525,
            "genau": 6060, "dashboard": 5050, "rfb": 0,
            # None hosted in this session, so neither its window nor either of
            # its region shows (which cover the players' rects, and are managed
            # roles for that reason).
            "origenerator": 0,
            "origenerator_portrait": 0, "origenerator_landscape": 0,
        }


class TestTheCoverStaysOnTopWhileTheRoomIsBanded:
    """Every promotion in the banding walk lands ABOVE the cover, so the cover
    has to be put back after each one.

    ``HWND_TOPMOST`` inserts at the top of the topmost band, and the cover is
    itself topmost — so a window promoted while it is up is over it until
    something puts it back.  Left to the cover's own 200ms poll, that is a
    window flashing through the scrim, and there is one per managed role.
    """

    ROLE_HWNDS = {"rfb": 11, "portrait": 22, "landscape": 33, "dashboard": 44,
                  "nau": 55, "genau": 66}
    COVER = 999

    def _calls(self, **kwargs):
        calls: list[tuple[int, bool]] = []
        with patch("fun_time.windows_bridge_sequencer.set_always_on_top",
                   side_effect=lambda h, v: calls.append((h, v))):
            windows_bridge_sequencer.apply_topmost_bands(
                dict(self.ROLE_HWNDS), "nau", **kwargs)
        return calls

    def test_the_cover_goes_back_on_top_after_every_promotion(self):
        calls = self._calls(beneath=self.COVER)

        promotions = [i for i, (h, on) in enumerate(calls)
                      if on and h != self.COVER]
        assert promotions, "nothing was promoted, so this proves nothing"
        for index in promotions:
            assert calls[index + 1] == (self.COVER, True), (
                f"{calls[index]} was left above the cover until the next "
                "SetWindowPos, which is long enough to see"
            )

    def test_the_walk_still_promotes_in_role_order(self):
        """Interleaving the cover must not disturb who ends up above whom: the
        order of the promotions is what puts Genau's HUD over Nau's video."""
        banded = [h for h, on in self._calls(beneath=self.COVER)
                  if on and h != self.COVER]
        plain = [h for h, on in self._calls() if on]

        assert banded == plain

    def test_a_demotion_needs_no_cover_re_assert(self):
        """Dropping out of the topmost band lands below the cover already, so
        there is nothing to put back — and re-asserting anyway would spend a
        SetWindowPos on every window the mode is hiding."""
        calls = self._calls(beneath=self.COVER)

        for index, (hwnd, on_top) in enumerate(calls):
            if not on_top:
                assert calls[index + 1:index + 2] != [(self.COVER, True)]

    def test_without_a_cover_nothing_extra_is_touched(self):
        """The re-band after the cover has gone, and the integration path, walk
        exactly the windows they are given."""
        assert all(h in self.ROLE_HWNDS.values() for h, _on in self._calls())


class TestPhase4Reveal:
    """Phase 4 (hide_windows only): play satellites, unpause Nau."""

    def _run_hidden(self, manifest_path, tmp_path, *, title_to_hwnd=None, topmost_calls=None):
        title_map = title_to_hwnd or {"Fun Time": 5050, "Genau": 6060}
        # Both players reporting frames: the curtain waits for that before it
        # comes down (a satellite's window exists long before mpv has drawn
        # anything into it), so a run with no status files would hold it up.
        manifest = configparser.ConfigParser()
        manifest.optionxform = str
        manifest.read(str(manifest_path), encoding="utf-8")
        for key in ("portrait_status_file", "landscape_status_file"):
            status = Path(manifest["commands"][key])
            status.parent.mkdir(parents=True, exist_ok=True)
            status.write_text("video=a.mp4\nposition_ms=250\n", encoding="utf-8")
        topmost_tracker = (lambda h, v: topmost_calls.append((h, v))) if topmost_calls is not None else (lambda h, v: None)
        hide_calls = self._hide_calls = []

        with _sequencer_stubs(wait_for_window_by_title=dict(side_effect=lambda title, **kw: title_map.get(title, 0)), set_always_on_top=dict(side_effect=topmost_tracker), minimize_window=dict(side_effect=lambda h, **kw: hide_calls.append(h))):
            return run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

    def test_every_player_is_still_held_when_the_phases_end(self, cfg_factory, tmp_path):
        """The path with a cover does not start playing when its phases end.

        The finishing pass — the bands, the settle — runs after this and behind
        the cover, so a player released here plays for seconds he can neither see
        nor hear, and the opening of the video is gone by the time the cover
        lifts.  The orchestrator calls ``release_the_players`` itself, once the
        cover is off the screen.
        """
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = LaunchManifest.read(manifest_path)
        held = _pause_every_player(m)

        self._run_hidden(manifest_path, tmp_path)

        for flag in held:
            assert flag.read_text(encoding="utf-8").strip() == "1", flag.name

    def test_the_release_starts_the_players_the_mode_shows(self, cfg_factory, tmp_path):
        """And what the orchestrator calls once the cover is gone does start them:
        Nau in nau mode, with Genau and its audio left parked."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = LaunchManifest.read(manifest_path)
        _pause_every_player(m)

        release_the_players(m, "nau")

        assert Path(m.commands.nau_paused_file).read_text(encoding="utf-8").strip() == "0"
        assert Path(m.commands.genau_paused_file).read_text(encoding="utf-8").strip() == "1"
        assert Path(m.commands.audio_paused_file).read_text(encoding="utf-8").strip() == "1"

    def test_nothing_is_promoted_topmost_while_the_loading_overlay_is_up(self, cfg_factory, tmp_path):
        """The whole point of the loading overlay is to hide the mess of starting
        seven windows.  ``SetWindowPos(hwnd, HWND_TOPMOST, …)`` inserts a window at
        the TOP of the topmost band — above the overlay, which is itself topmost —
        so every promotion here flashes that window over the overlay until the
        overlay's next 200ms poll re-asserts itself.  The bands go on once the
        overlay is destroyed, in ``_fix_post_loading_windows``."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        topmost_calls: list[tuple] = []
        self._run_hidden(
            manifest_path, tmp_path, topmost_calls=topmost_calls,
        )

        assert topmost_calls == []

    def test_the_idle_slot_mate_is_still_parked_behind_the_overlay(self, cfg_factory, tmp_path):
        """Visibility is settled behind the overlay even though the bands are not:
        minimizing Genau moves no window into the topmost band, so it cannot flash."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        self._run_hidden(manifest_path, tmp_path)

        NAU_HWND, GENAU_HWND = 2525, 6060
        assert set(self._hide_calls) == {GENAU_HWND}
        assert NAU_HWND not in self._hide_calls

class TestNauGatesTheReveal:
    """The overlay must not come down over Nau's own loading screen.

    Nau opens its window before it reads its library, so the caption lookup that
    stood for "Nau is up" now answers while Nau is still loading and painting its
    own progress bar.  Standalone, that screen is Nau's to show; inside Fun Time
    the wait belongs to Fun Time, and the phase named for it — "Waiting for
    players..." — is where it goes.  Nau is the third player, and the only one
    still loading by then.
    """

    def test_the_players_phase_covers_the_wait_for_nau(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        events: list[str] = []

        def track_wait(status_file, *_args, **_kwargs):
            events.append(f"wait-for-nau:{status_file}")
            return True

        with _sequencer_stubs(wait_for_window_by_title=dict(return_value=88888), _wait_for_nau_loaded=dict(side_effect=track_wait)):
            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=_TrackingProgress(events),
                hide_windows=True,
            )

        # Inside the players phase, and on Nau's own status file — not after
        # "windows", where the bar would sit under "Positioning windows..."
        # through a wait that positions nothing.
        assert events == [
            "services",
            "browser",
            "companions",
            "players",
            f"wait-for-nau:{cfg.nau_status_file}",
            "windows",
            "finalizing",
        ]

    def test_the_stale_status_is_read_for_the_resume_and_only_then_dropped(
        self, cfg_factory, tmp_path,
    ):
        """Dropping last session's status file is what makes the next one mean
        something — without it the wait ends at once on a video from a session
        that is over.  But startup also resumes Nau onto the video that same file
        names, so the drop has to fall between that read and Nau's launch.
        """
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        status_file = Path(cfg.nau_status_file)
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text("video=last_session.mp4\n", encoding="utf-8")

        seen: dict = {}

        def capture_core(**kwargs):
            seen["resumed_onto"] = read_nau_status(Path(kwargs["nau_status_file"])).video
            _write_result(kwargs["result_file"], CORE_PIDS)

        def capture_nau(**kwargs):
            seen["stale_at_launch"] = Path(kwargs["status_file"]).exists()
            return _fake_nau(**kwargs)

        with _sequencer_stubs(start_core_session=dict(side_effect=capture_core), launch_nau=dict(side_effect=capture_nau), wait_for_window_by_title=dict(return_value=88888)):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        assert seen["resumed_onto"] == "last_session.mp4"
        assert seen["stale_at_launch"] is False

    def test_the_wait_for_the_players_cannot_outlast_the_overlay(self):
        """The overlay tears itself down when the progress file has gone
        STALE_TIMEOUT_S without changing — its guard against an orchestrator that
        died.  The file is written when a phase STARTS, so a players phase able to
        run longer than that guard would drop the overlay mid-wait and reveal the
        very loading screen it is waiting out.
        """
        assert WINDOW_RESOLVE_TIMEOUT_S + NAU_LOAD_TIMEOUT_S < STALE_TIMEOUT_S


FAKE_LAYOUT_CFG = LayoutConfig(
    primary_monitor=0,
    secondary_monitor=1,
    main_top_ratio=0.48,
    landscape_width_ratio=0.35,
)

MAIN_RECT = MonitorRect(x=0, y=0, width=2560, height=1392)


class TestResolveShortcut:
    """The .lnk resolver behind the Random Favs Browser launch.

    It swallows every exception twice and hands back empty strings, which the
    caller turns into one 'skipped' log line — a failure mode nothing louder
    can catch, so what CAN be pinned off Windows is pinned here: the
    PowerShell fallback's parsing, and the all-quiet dead end.  (The COM fast
    path and the real EnumWindows enumeration are Windows-only flesh; the
    integration suite is their only cover.)
    """

    @staticmethod
    def _without_com(monkeypatch):
        """Force the win32com import to fail, as it does off Windows — and so
        the test means the same thing on Windows CI, where it would otherwise
        answer from real COM."""
        import sys as _sys

        monkeypatch.setitem(_sys.modules, "win32com", None)
        monkeypatch.setitem(_sys.modules, "win32com.client", None)

    def test_parses_the_three_fields_powershell_reports(self, monkeypatch):
        self._without_com(monkeypatch)
        completed = SimpleNamespace(
            stdout="C:\\Chrome\\chrome.exe\r\nC:\\Chrome\r\n--profile-directory=\"Profile 2\"\r\n",
            returncode=0,
        )
        with patch("fun_time.windows_bridge_sequencer.subprocess.run",
                   return_value=completed):
            resolved = windows_bridge_sequencer.resolve_shortcut(r"C:\fake\s.lnk")

        assert resolved == (
            "C:\\Chrome\\chrome.exe", "C:\\Chrome", '--profile-directory="Profile 2"',
        )

    def test_a_bare_target_resolves_without_workdir_or_args(self, monkeypatch):
        self._without_com(monkeypatch)
        completed = SimpleNamespace(stdout="C:\\Chrome\\chrome.exe\r\n", returncode=0)
        with patch("fun_time.windows_bridge_sequencer.subprocess.run",
                   return_value=completed):
            assert windows_bridge_sequencer.resolve_shortcut(r"C:\fake\s.lnk") == (
                "C:\\Chrome\\chrome.exe", "", "",
            )

    def test_every_resolver_failing_is_three_empty_strings_not_a_raise(self, monkeypatch):
        self._without_com(monkeypatch)
        with patch("fun_time.windows_bridge_sequencer.subprocess.run",
                   side_effect=OSError("no powershell")):
            assert windows_bridge_sequencer.resolve_shortcut(r"C:\fake\s.lnk") == ("", "", "")


    def test_each_link_that_fails_says_so_before_the_next_one_is_tried(
            self, monkeypatch, caplog):
        """"Random Favs Browser skipped: could not resolve shortcut" was the
        whole account of a failure with two resolvers behind it, so the one
        question worth asking — which link broke, and how — had no answer
        anywhere.  Each fall-through now says which resolver it was and what it
        raised, at debug, so the working case stays silent."""
        self._without_com(monkeypatch)
        with caplog.at_level(logging.DEBUG, logger="fun_time.windows_bridge_sequencer"), \
             patch("fun_time.windows_bridge_sequencer.subprocess.run",
                   side_effect=OSError("no powershell")):
            assert windows_bridge_sequencer.resolve_shortcut(r"C:\fake\s.lnk") == ("", "", "")

        said = " ".join(record.getMessage() for record in caplog.records)
        assert "COM" in said and "PowerShell" in said
        assert any(record.exc_info for record in caplog.records), (
            "the fall-through has to carry what was raised, or it explains nothing")


class TestWaitForNewChromeWindow:
    """The poll that pairs a launch with the window it opened."""

    def _clock(self, monkeypatch):
        ticks = iter(range(1000))
        monkeypatch.setattr(
            windows_bridge_sequencer, "time",
            SimpleNamespace(monotonic=lambda: float(next(ticks)), sleep=lambda _s: None),
        )

    def test_returns_the_window_that_was_not_there_before(self, monkeypatch):
        self._clock(monkeypatch)
        snapshots = iter([{111, 222}, {111, 222}, {111, 222, 333}])
        with patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds",
                   side_effect=lambda: next(snapshots)):
            hwnd = windows_bridge_sequencer._wait_for_new_chrome_window(
                {111, 222}, timeout_ms=8000)

        assert hwnd == 333

    def test_gives_up_at_the_deadline_when_no_window_appears(self, monkeypatch):
        self._clock(monkeypatch)
        with patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds",
                   return_value={111, 222}):
            hwnd = windows_bridge_sequencer._wait_for_new_chrome_window(
                {111, 222}, timeout_ms=3000)

        assert hwnd == 0


class TestMaybeLaunchRandomFavsBrowser:
    """Regression: browser must launch (bug #3) and be positioned at its planned rect."""

    def _browser_settings(self, *, enabled: bool = True) -> RandomFavsBrowserSettings:
        return RandomFavsBrowserSettings(
            enabled=enabled,
            shortcut_path=r"C:\fake\shortcut.lnk",
            manifest_file=r"C:\fake\manifest.ini",
        )

    def _fake_plan(self) -> WindowLayoutPlan:
        """Build a minimal plan with a random_favs_browser rect."""
        from fun_time.window_layout import compute_window_layout
        return compute_window_layout(
            primary_monitor=MAIN_RECT,
            secondary_monitor=MonitorRect(x=2560, y=0, width=1440, height=3440),
            layout_config=FAKE_LAYOUT_CFG,
        )

    def test_skipped_when_disabled(self):
        """When disabled=0, no browser launch or window positioning happens."""
        m = self._browser_settings(enabled=False)
        plan = self._fake_plan()
        move_calls: list[tuple] = []

        with patch("fun_time.windows_bridge_sequencer.move_window",
                    side_effect=lambda *a, **kw: move_calls.append(a)):
            rfb_hwnd = _maybe_launch_random_favs_browser(m, plan)

        assert move_calls == []
        assert rfb_hwnd == 0

    def test_launches_and_positions_browser(self):
        m = self._browser_settings()
        plan = self._fake_plan()
        browser_rect = plan.random_favs_browser

        launch_result = MagicMock(should_launch=True)

        with patch("fun_time.windows_bridge_sequencer.resolve_shortcut", return_value=("chrome.exe", "", "")), \
             patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_favs_browser", return_value=launch_result), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window") as mock_move:
            rfb_hwnd = _maybe_launch_random_favs_browser(m, plan)

        # Browser window should be positioned at the planned rect
        mock_move.assert_called_once_with(
            55555, browser_rect.x, browser_rect.y, browser_rect.width, browser_rect.height,
            activate=True,
        )
        # Should return the browser hwnd for topmost management
        assert rfb_hwnd == 55555

    def test_launches_the_urls_the_manifest_already_resolved(self):
        """Lazy loading is settled when the manifest is built, not at launch."""
        m = self._browser_settings()
        plan = self._fake_plan()

        launch_kwargs: dict = {}
        launch_result = MagicMock(should_launch=True)

        def capture_launch(*args, **kwargs):
            launch_kwargs.update(kwargs)
            return launch_result

        with patch("fun_time.windows_bridge_sequencer.resolve_shortcut", return_value=("chrome.exe", "", "")), \
             patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_favs_browser", side_effect=capture_launch), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window"):
            _maybe_launch_random_favs_browser(m, plan)

        assert set(launch_kwargs) == {"shortcut"}
        assert launch_kwargs["shortcut"] == ChromeShortcut(
            target="chrome.exe", work_dir="", args="")


class TestResolveSatelliteHwnds:
    """A satellite window is found by its DISTINCT caption, and by nothing else.

    Its pid cannot find it: ``Popen`` returns the venv's ``Scripts\\pythonw.exe``
    launcher, which spawns the base interpreter as a CHILD, and that child owns the
    window — so a pid poll here never resolves and always burns its whole timeout.
    Distinct captions are also what keep the lookup from crossing the two, which
    was the portrait/landscape visual swap.
    """

    def test_resolves_each_side_by_its_distinct_title(self):
        title_to_hwnd = {"Portrait AI Player": 1111, "Landscape AI Player": 2222}

        with patch(
            "fun_time.windows_bridge_sequencer.wait_for_window_by_title",
            side_effect=lambda title, **kw: title_to_hwnd.get(title, 0),
        ) as by_title:
            portrait, landscape = _resolve_satellite_hwnds()

        # The portrait window lands in the portrait slot, the landscape in the
        # landscape slot — never crossed.
        assert (portrait, landscape) == (1111, 2222)
        # Resolved by the two DISTINCT captions, never the shared "Satellite" that
        # made the lookup ambiguous, and each lookup is exact.
        resolved = {call.args[0] for call in by_title.call_args_list}
        assert resolved == {"Portrait AI Player", "Landscape AI Player"}
        assert all(call.kwargs.get("exact") is True for call in by_title.call_args_list)


class TestWaitForNauLoaded:
    """Nau's window is not the signal that Nau is ready.

    Nau opens its window within half a second of launch and reads its library
    behind it — one ffprobe per unprobed video on a cold cache, tens of seconds —
    painting its OWN loading screen into it meanwhile.  So a caption lookup
    returns while Nau is still loading.  Its status file does not: Nau writes
    that from its playback loop, once a video is up.
    """

    def test_returns_once_nau_reports_a_video(self, tmp_path):
        status_file = tmp_path / "nau_status.txt"

        def nau_finishes_loading(_seconds):
            status_file.write_text("video=clip.mp4\n", encoding="utf-8")

        # Absent on the first look, so it can only return by polling again.
        with patch("fun_time.windows_bridge_sequencer.time.sleep",
                   side_effect=nau_finishes_loading):
            assert _wait_for_nau_loaded(status_file, NullProgress()) is True

    def test_a_status_file_naming_no_video_is_not_a_loaded_nau(self, tmp_path):
        """Nau writes its status whole, but a poll can catch that first write
        half-done.  So the wait reads the video out rather than taking the file's
        mere existence for the signal, and an empty read keeps it waiting.
        """
        status_file = tmp_path / "nau_status.txt"
        status_file.write_text("", encoding="utf-8")

        with patch("fun_time.windows_bridge_sequencer.time.sleep"):
            assert _wait_for_nau_loaded(
                status_file, NullProgress(), timeout_s=0.3,
            ) is False

    def test_a_nau_that_never_loads_gives_the_desktop_up_rather_than_keep_it(self, tmp_path):
        """A crashed Nau must not wedge startup behind an overlay forever: the
        wait is bounded, and past its budget the session is revealed without it.
        """
        with patch("fun_time.windows_bridge_sequencer.time.sleep"):
            assert _wait_for_nau_loaded(
                tmp_path / "never.txt", NullProgress(), timeout_s=0.0,
            ) is False

    def test_esc_is_answered_inside_the_wait_not_at_the_end_of_it(self, tmp_path):
        """This is the one stretch of startup that can run for tens of seconds,
        and the overlay covering it says "Press Esc to cancel".  Checked only at
        the next phase boundary, that Esc would go unanswered for the whole of
        the wait it is most likely to be pressed during.
        """
        class Cancelled:
            cancelled = True
            def advance(self, phase: str) -> None: pass
            def finish(self) -> None: pass

        with patch("fun_time.windows_bridge_sequencer.time.sleep") as slept:
            with pytest.raises(StartupCancelled):
                _wait_for_nau_loaded(tmp_path / "never.txt", Cancelled())

        slept.assert_not_called()


class TestOrigeneratorLaunch:
    def test_a_configured_origenerator_launches_with_the_layout(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
            "origenerator_python_exe": str(tmp_path / "py" / "python.exe"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return 91

        with _sequencer_stubs(launch_origenerator=dict(side_effect=capture)):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_pid == 91
        assert captured["origenerator_dir"] == str(tmp_path / "origenerator")
        assert captured["python_exe"] == str(tmp_path / "py" / "python.exe")
        assert captured["layout_plan"].random_favs_browser.width > 0
        assert str(captured["command_file"]).endswith("origenerator_cmd.txt")
        # A "1" a prior session's OmniPause stranded in the paused flag is
        # cleared before the app launches — a stale freeze made every show
        # open frozen while the room ran.
        assert Path(captured["paused_file"]).read_text(encoding="utf-8") == "0"

    def test_without_a_configured_origenerator_nothing_launches(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        with _sequencer_stubs(launch_origenerator=dict()) as stubs:
            launch = stubs.launch_origenerator
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        launch.assert_not_called()
        assert result.origenerator_pid == 0


class TestOrigeneratorBehindTheOverlay:
    def test_a_resumed_origenerator_session_restores_the_window_before_the_reveal(
        self, cfg_factory, tmp_path
    ):
        """The loading screen exists so the room is set up before it is seen —
        the hosted window used to pop up seconds after the reveal.  A session
        opening in origenerator mode now holds the overlay for that window,
        restores it behind the curtain, and carries the mode out so the
        post-overlay pass bands it over the RFB."""
        from fun_time.shared_state import BridgeState
        from fun_time.shared_state import shared_state_path, write_shared_state

        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        write_shared_state(shared_state_path(tmp_path),
                           BridgeState(satellites_mode="origenerator"))

        with _sequencer_stubs(launch_origenerator=dict(return_value=91), find_window_for_process=dict(return_value=7171), restore_window=dict()) as stubs:
            resolve = stubs.find_window_for_process
            restore = stubs.restore_window
            result = run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        resolve.assert_called_with(91, "Origenerator")
        restore.assert_called_once_with(7171, activate=False)
        assert result.satellites_mode == "origenerator"
        assert result.role_hwnds["origenerator"] == 7171
        # And the mode means both regions PLAYING: the same OPEN_SHOWS the
        # switch into the mode sends, so a resumed session comes up on the
        # library of each region's shape rather than on two black rectangles.
        assert (cfg.paths.state_dir / "origenerator_cmd.txt").read_text(
            encoding="utf-8").split() == ["OPEN_SHOWS"]

    def test_a_player_mode_session_never_waits_on_the_hosted_boot(
        self, cfg_factory, tmp_path
    ):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        with _sequencer_stubs(launch_origenerator=dict(return_value=91), find_window_for_process=dict(), restore_window=dict()) as stubs:
            resolve = stubs.find_window_for_process
            restore = stubs.restore_window
            result = run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        resolve.assert_not_called()   # the parked window is the mode's own state
        restore.assert_not_called()
        assert result.satellites_mode == "player"
        # And nothing asks it to fill the regions — including anything a prior
        # session left unread, which the launch clears for exactly this reason:
        # the app drains that file on its first tick, so a stranded OPEN_SHOWS
        # would fill the regions of a session that opened in player mode.
        assert (cfg.paths.state_dir / "origenerator_cmd.txt").read_text(encoding="utf-8") == ""

    def test_a_stranded_verb_is_cleared_before_the_hosted_app_can_read_it(
        self, cfg_factory, tmp_path
    ):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        (cfg.paths.state_dir / "origenerator_cmd.txt").write_text(
            "PORTRAIT_NEXT\n", encoding="utf-8")  # last session's, never drained

        with _sequencer_stubs(launch_origenerator=dict(return_value=91)):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        assert (cfg.paths.state_dir / "origenerator_cmd.txt").read_text(encoding="utf-8") == ""


class TestWaitingForThePlayersToDraw:
    """The curtain comes down on a room that is ready to look at.

    A satellite's window exists within a second of launch and stays BLACK until
    mpv has opened its first clip — several seconds on the 4K landscape library
    — so a reveal timed on the windows alone lifts on two black rectangles that
    fill in afterwards.  Its status file says ``position_ms`` once frames are
    actually going out, which is what this waits for.
    """

    def test_it_waits_until_every_player_reports_frames(self, tmp_path):
        portrait = tmp_path / "portrait_status.txt"
        landscape = tmp_path / "landscape_status.txt"
        portrait.write_text("video=a.mp4\nposition_ms=120\n", encoding="utf-8")
        landscape.write_text("video=b.mp4\nposition_ms=0\n", encoding="utf-8")
        progress = SimpleNamespace(cancelled=False)

        assert _wait_for_players_drawing(
            (portrait, landscape), progress, timeout_s=0.3) is False

        landscape.write_text("video=b.mp4\nposition_ms=90\n", encoding="utf-8")
        assert _wait_for_players_drawing(
            (portrait, landscape), progress, timeout_s=0.3) is True

    def test_a_player_that_never_draws_does_not_keep_the_desktop(self, tmp_path):
        """Bounded like Nau's wait: a player stuck on a bad clip must not hold
        the curtain up forever — the reveal goes ahead and the log says why."""
        never = tmp_path / "portrait_status.txt"
        progress = SimpleNamespace(cancelled=False)

        assert _wait_for_players_drawing((never,), progress, timeout_s=0.2) is False

    def test_it_is_a_cancellation_checkpoint(self, tmp_path):
        """Esc on the loading screen has to land during this stretch too — it is
        one of the few that can run for tens of seconds."""
        progress = SimpleNamespace(cancelled=True)

        with pytest.raises(StartupCancelled):
            _wait_for_players_drawing((tmp_path / "s.txt",), progress, timeout_s=1.0)
