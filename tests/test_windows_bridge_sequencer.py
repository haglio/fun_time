from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock

from fun_time.config import load_config
from fun_time.dashboard_runtime import read_nau_status
from fun_time.loading_screen import STALE_TIMEOUT_S
from fun_time.manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.nau_console import nau_console_path
from fun_time import windows_bridge_sequencer
from fun_time.windows_bridge_sequencer import (
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

# Primary slot on the secondary monitor with conftest's primary_top_ratio=0.727:
# portrait height = int(3440 * 0.727) = 2500, primary height = 940.
PRIMARY_MEDIA_RECT = {"x": 2560, "y": 2500, "width": 1440, "height": 940}


def _make_manifest(cfg_factory, tmp_path):
    cfg = load_config(cfg_factory())
    manifest_path = write_windows_bridge_manifest(
        cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )
    return cfg, manifest_path


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


def _fake_ui(**kwargs):
    _write_result(kwargs["result_file"], UI_PIDS)


def _fake_nau(**kwargs):
    """Nau, launched: its status file appears once it has a video up.

    The overlay is held on exactly that file, so a fake that returned a pid and
    wrote nothing would leave every startup here waiting out the full budget.
    """
    Path(kwargs["status_file"]).write_text("video=this_session.mp4\n", encoding="utf-8")
    return NAU_PID


class TestRunStartupSequence:
    def test_calls_start_core_session_and_launch_ui_companions(self, cfg_factory, tmp_path):
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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=capture_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=capture_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=99999), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.nau_pid == NAU_PID
        assert result.portrait_pid == 30
        assert result.landscape_pid == 40
        assert result.dashboard_pid == 50
        assert result.genau_pid == GENAU_PID
        assert result.audio_pid == 70

        # The native satellites are wired from the manifest: OUR python (the
        # satellite player ships from this repo, so it must not depend on
        # genau's venv), the satellite module, and each side's files.
        assert core_called["satellite_python_exe"] == str(cfg.paths.python_exe)
        assert core_called["satellite_python_exe"] != str(cfg.paths.genau_python_exe)
        assert core_called["satellite_module"] == "satellite"
        state = cfg.paths.state_dir
        for side in ("portrait", "landscape"):
            assert core_called[f"{side}_cmd_file"] == str(state / f"{side}_cmd.txt")
            assert core_called[f"{side}_paused_file"] == str(state / f"{side}_paused.txt")
            assert core_called[f"{side}_status_file"] == str(state / f"{side}_status.txt")
            # Each windowed player also gets a log to write its stdout+stderr to:
            # under pythonw there is no console, so without one an unhandled
            # exception kills it leaving no traceback anywhere.
            assert core_called[f"{side}_log_file"] == tmp_path / f"{side}_satellite.log"
        # Nau's status file rides along too: startup resumes each player onto the
        # video its status file names, and Nau is the third of the three.
        assert core_called["nau_status_file"] == str(cfg.nau_status_file)
        # The satellites launch straight into their real layout rects (mpv won't
        # rescale on a later Win32 resize), so the sequencer threads the computed
        # portrait/landscape rects into the core launch — this is what makes the
        # native video fill its window.
        assert core_called["portrait_rect"] == result.layout_plan.portrait
        assert core_called["landscape_rect"] == result.layout_plan.landscape
        assert core_called["favs_file"] == str(cfg.paths.favs_file)
        assert core_called["state_dir"] == tmp_path
        assert core_called["nau_paused_file"] == str(cfg.nau_paused_file)
        # Provider roots flow through so the startup build can collapse action groups.
        assert core_called["regen_media_root"] == tmp_path / "media"
        assert core_called["regen_metadata_root"] == tmp_path / "metadata"
        # The broker heartbeat path flows through so startup can leave a live
        # broker running instead of killing and relaunching it.
        assert core_called["broker_heartbeat_file"] == str(cfg.broker_heartbeat_file)
        # And its command path, so startup can park the OSR2 for the long wait.
        assert core_called["broker_cmd_file"] == str(cfg.broker_cmd_file)
        # MFP is gone: no mfp_exe/mfp_pid plumbing anywhere.
        assert not any("mfp" in key for key in core_called)
        assert not any("mfp" in key for key in ui_called)
        assert ui_called["dashboard_enabled"] is True
        # The RFB rect is forwarded so the reference popup can fill that space.
        assert {"rfb_x", "rfb_y", "rfb_width", "rfb_height"} <= set(ui_called)
        assert all(
            isinstance(ui_called[key], int)
            for key in ("rfb_x", "rfb_y", "rfb_width", "rfb_height")
        )
        # The log stream is embedded in the dashboard window now, so there is no
        # separate log-panel rect to forward.
        assert not any(key.startswith("log_") for key in ui_called)

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", side_effect=capture_genau), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=capture_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # Genau receives its manifest file paths and the shared primary rect.
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
            "dashboard_cmd_file": str(cfg.paths.state_dir / "dashboard_cmd.txt"),
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
        }

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", side_effect=lambda title, **kw: title_to_hwnd.get(title, 0)), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=lambda hwnd, x, y, w, h, **_kw: move_calls.append((hwnd, x, y, w, h))), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # The two satellite windows are positioned immediately in normal mode.
        moved_hwnds = {c[0] for c in move_calls}
        assert {3030, 4040} <= moved_hwnds

        # nau startup mode: the windows that own a rect are promoted to topmost,
        # Nau (2525) included so it floats above the desktop like the primary
        # player always has.  Genau (6060) is the hidden slot-mate and stays out
        # of the band — it is promoted last, so being in it puts it over Nau.
        promoted = {h for h, on in topmost_calls if on}
        assert promoted == {3030, 4040, 2525}

    def test_returns_layout_plan(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.layout_plan is not None
        assert result.layout_plan.portrait.x == 2560
        assert result.layout_plan.dashboard.width > 0

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path, hide_windows=False)

        assert nau_paused.read_text(encoding="utf-8").strip() == "0"


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


class TestRunStartupSequenceCancellation:
    def test_cancel_before_companions_reports_only_the_core_children(self, cfg_factory, tmp_path):
        """Cancelling at the layout checkpoint (2nd advance) has launched the
        core stack — the satellites, Genau and Nau — but not the companions."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        ui = MagicMock()

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer._maybe_launch_random_favs_browser", return_value=7777), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=lambda *a, **kw: move_activates.append(kw.get("activate", True))), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert move_activates, "Windows should still be positioned in integration mode"
        assert all(activate is False for activate in move_activates), \
            f"move_window must not activate during integration: {move_activates}"

    def test_activates_windows_outside_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        move_activates: list[bool] = []

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=lambda *a, **kw: move_activates.append(kw.get("activate", True))), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)
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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title",
                   side_effect=lambda title, **kw: title_to_hwnd.get(title, 88888)), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=track_move), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title",
                   side_effect=lambda title, **kw: title_to_hwnd.get(title, 0)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.disable_window_transitions"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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
        }


class TestPhase4Reveal:
    """Phase 4 (hide_windows only): play satellites, unpause Nau."""

    def _run_hidden(self, manifest_path, tmp_path, *, pid_to_hwnd=None, title_to_hwnd=None, topmost_calls=None):
        pid_map = pid_to_hwnd or {30: 3030, 40: 4040, NAU_PID: 2525, 50: 5050}
        title_map = title_to_hwnd or {"Fun Time": 5050, "Genau": 6060}
        topmost_tracker = (lambda h, v: topmost_calls.append((h, v))) if topmost_calls is not None else (lambda h, v: None)
        hide_calls = self._hide_calls = []

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", side_effect=lambda title, **kw: title_map.get(title, 0)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=topmost_tracker), \
             patch("fun_time.windows_bridge_sequencer.minimize_window", side_effect=lambda h, **kw: hide_calls.append(h)), \
             patch("fun_time.windows_bridge_sequencer.disable_window_transitions"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            return run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

    def test_unpauses_nau_and_keeps_genau_parked(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = configparser.ConfigParser()
        m.optionxform = str
        m.read(str(manifest_path), encoding="utf-8")
        # Start all three flags paused, as seed_startup_states does
        for key in ("genau_paused_file", "audio_paused_file", "nau_paused_file"):
            flag = Path(m["commands"][key])
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("1", encoding="utf-8")

        self._run_hidden(manifest_path, tmp_path)

        # The reveal: Nau is unpaused so it starts playing when the loading
        # screen comes down; Genau and audio stay parked.
        assert Path(m["commands"]["nau_paused_file"]).read_text(encoding="utf-8").strip() == "0"
        assert Path(m["commands"]["genau_paused_file"]).read_text(encoding="utf-8").strip() == "1"
        assert Path(m["commands"]["audio_paused_file"]).read_text(encoding="utf-8").strip() == "1"

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

    def test_dashboard_found_by_title_is_resolved_for_the_role_cache(self, cfg_factory, tmp_path):
        """find_window_by_pid fails for the dashboard because the venv launcher
        PID differs from the Qt window's PID — Phase 4 must fall back to the
        exact title lookup ("Fun Time"), because this is the last moment the
        window is resolvable before it is hidden behind the overlay."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        DASH_HWND = 5050
        # No entry for the dashboard pid (50): pid lookup returns 0
        pid_to_hwnd = {30: 3030, 40: 4040, NAU_PID: 2525}
        title_to_hwnd = {"Fun Time": DASH_HWND, "Genau": 6060}

        result = self._run_hidden(
            manifest_path, tmp_path,
            pid_to_hwnd=pid_to_hwnd, title_to_hwnd=title_to_hwnd,
        )

        assert result.role_hwnds["dashboard"] == DASH_HWND


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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=_fake_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer._wait_for_nau_loaded", side_effect=track_wait), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=capture_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=capture_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.minimize_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

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
    main_monitor=0,
    secondary_monitor=1,
    primary_top_ratio=0.48,
    landscape_width_ratio=0.35,
)

MAIN_RECT = MonitorRect(x=0, y=0, width=2560, height=1392)


class TestMaybeLaunchRandomFavsBrowser:
    """Regression: browser must launch (bug #3) and be positioned at its planned rect."""

    def _make_manifest_parser(self, *, enabled: str = "1") -> configparser.ConfigParser:
        m = configparser.ConfigParser()
        m.optionxform = str
        m["random_favs_browser"] = {
            "enabled": enabled,
            "shortcut_path": r"C:\fake\shortcut.lnk",
            "manifest_file": r"C:\fake\manifest.ini",
        }
        return m

    def _fake_plan(self) -> WindowLayoutPlan:
        """Build a minimal plan with a random_favs_browser rect."""
        from fun_time.window_layout import compute_window_layout
        return compute_window_layout(
            main_monitor=MAIN_RECT,
            secondary_monitor=MonitorRect(x=2560, y=0, width=1440, height=3440),
            layout_config=FAKE_LAYOUT_CFG,
        )

    def test_skipped_when_disabled(self):
        """When disabled=0, no browser launch or window positioning happens."""
        m = self._make_manifest_parser(enabled="0")
        plan = self._fake_plan()
        move_calls: list[tuple] = []

        with patch("fun_time.windows_bridge_sequencer.move_window",
                    side_effect=lambda *a, **kw: move_calls.append(a)):
            rfb_hwnd = _maybe_launch_random_favs_browser(m, plan)

        assert move_calls == []
        assert rfb_hwnd == 0

    def test_launches_and_positions_browser(self, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        m = self._make_manifest_parser()
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

    def test_launches_the_urls_the_manifest_already_resolved(self, monkeypatch):
        """Lazy loading is settled when the manifest is built, not at launch."""
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        m = self._make_manifest_parser()
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

        assert set(launch_kwargs) == {"shortcut_target", "shortcut_work_dir", "shortcut_args"}


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
