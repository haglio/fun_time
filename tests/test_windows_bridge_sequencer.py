from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock

from fun_time.config import load_config
from fun_time.manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.windows_bridge_sequencer import (
    run_startup_sequence,
    _maybe_launch_random_favs_browser,
)
from fun_time.monitors import MonitorInfo
from fun_time.window_layout import (
    MonitorRect,
    WindowLayoutPlan,
)
from fun_time.config import LayoutConfig
from fun_time.startup_progress import NullProgress


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
        cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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


class TestRunStartupSequence:
    def test_calls_start_core_session_and_launch_ui_companions(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({
            "provider_regen": {
                "media_root": str(tmp_path / "media"),
                "metadata_root": str(tmp_path / "metadata"),
            }
        }))
        manifest_path = write_windows_bridge_manifest(
            cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
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
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=capture_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=99999), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=99999), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=99999), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
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

        assert core_called["password"] == "testpw"
        assert core_called["favs_file"] == str(cfg.paths.favs_file)
        assert core_called["state_dir"] == tmp_path
        assert core_called["nau_paused_file"] == str(cfg.nau_paused_file)
        # Provider roots flow through so the startup build can collapse action groups.
        assert core_called["provider_media_root"] == tmp_path / "media"
        assert core_called["provider_metadata_root"] == tmp_path / "metadata"
        # MFP is gone: no mfp_exe/mfp_pid plumbing anywhere.
        assert not any("mfp" in key for key in core_called)
        assert not any("mfp" in key for key in ui_called)
        assert ui_called["dashboard_enabled"] is True

    def test_launches_genau_and_nau_with_primary_media_rect(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        genau_kwargs = {}
        nau_kwargs = {}

        def capture_genau(**kwargs):
            genau_kwargs.update(kwargs)
            return GENAU_PID

        def capture_nau(**kwargs):
            nau_kwargs.update(kwargs)
            return NAU_PID

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", side_effect=capture_genau), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", side_effect=capture_nau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # Genau receives its manifest file paths and the shared primary rect.
        assert genau_kwargs["command_file"] == str(cfg.genau_cmd_file)
        assert genau_kwargs["paused_file"] == str(cfg.genau_paused_file)
        assert genau_kwargs["clips_folder"] == str(cfg.paths.clips_dir)
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
            "nau_x": PRIMARY_MEDIA_RECT["x"],
            "nau_y": PRIMARY_MEDIA_RECT["y"],
            "nau_width": PRIMARY_MEDIA_RECT["width"],
            "nau_height": PRIMARY_MEDIA_RECT["height"],
        }

    def test_positions_vlc_windows_and_applies_topmost_policy(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        pid_to_hwnd = {30: 3030, 40: 4040, NAU_PID: 2525}
        title_to_hwnd = {"Genau": 6060}
        move_calls: list[tuple] = []
        topmost_calls: list[tuple] = []

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", side_effect=lambda pid, **kw: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid, **kw: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", side_effect=lambda title, **kw: title_to_hwnd.get(title, 0)), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=lambda hwnd, x, y, w, h, **_kw: move_calls.append((hwnd, x, y, w, h))), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # The two satellite VLC windows are positioned immediately in normal mode.
        moved_hwnds = {c[0] for c in move_calls}
        assert {3030, 4040} <= moved_hwnds

        # nau startup mode: every managed window is promoted to topmost — Nau
        # (2525) included, so it floats above the desktop like the primary
        # player always has.
        promoted = {h for h, on in topmost_calls if on}
        assert promoted == {3030, 4040, 6060, 2525}

    def test_returns_layout_plan(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
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
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path, hide_windows=False)

        assert nau_paused.read_text(encoding="utf-8").strip() == "0"


class TestNoActivateWindowDuringIntegration:
    """During integration tests, window moves must not steal focus."""

    def test_moves_windows_without_activation_in_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        move_activates: list[bool] = []

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=lambda *a, **kw: move_activates.append(kw.get("activate", True))), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
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
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=lambda *a, **kw: move_activates.append(kw.get("activate", True))), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert any(activate is True for activate in move_activates), \
            "move_window should activate core windows in normal mode"


class TestProgressReporting:
    """run_startup_sequence reports progress via the callback."""

    def test_hide_windows_advance_count_matches_total_steps(self, cfg_factory, tmp_path):
        """In hide_windows mode the number of advance() calls must equal
        _STARTUP_PROGRESS_STEPS so the progress bar reaches 100%.
        """
        from fun_time.windows_bridge_orchestrator import _STARTUP_PROGRESS_STEPS

        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        advance_messages: list[str] = []

        class TrackingProgress:
            def advance(self, message: str) -> None:
                advance_messages.append(message)
            def finish(self) -> None:
                pass

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=TrackingProgress(),
                hide_windows=True,
            )

        assert advance_messages == [
            "Preparing services...",
            "Computing window layout...",
            "Launching browser...",
            "Launching companions...",
            "Positioning windows...",
            "Finalizing...",
        ]
        assert len(advance_messages) == _STARTUP_PROGRESS_STEPS, (
            f"hide_windows fires {len(advance_messages)} steps "
            f"but _STARTUP_PROGRESS_STEPS={_STARTUP_PROGRESS_STEPS} — "
            f"progress bar reaches {100 * len(advance_messages) // _STARTUP_PROGRESS_STEPS}%"
        )

    def test_normal_mode_reports_each_startup_step(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        advance_messages: list[str] = []

        class TrackingProgress:
            def advance(self, message: str) -> None:
                advance_messages.append(message)
            def finish(self) -> None:
                pass

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=TrackingProgress(),
            )

        assert advance_messages == [
            "Preparing services...",
            "Computing window layout...",
            "Positioning windows...",
            "Finalizing window layout...",
            "Launching browser...",
            "Launching companions...",
        ]

    def test_null_progress_accepted_silently(self, cfg_factory, tmp_path):
        """NullProgress should work as a no-op."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
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

    def test_defers_positioning_and_collects_hwnds(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        pid_to_hwnd = {30: 3030, 40: 4040, NAU_PID: 2525, 50: 5050}
        move_calls: list[tuple] = []
        move_activates: list[bool] = []

        def track_move(hwnd, x, y, w, h, **kw):
            move_calls.append((hwnd, x, y, w, h))
            move_activates.append(kw.get("activate", True))

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", side_effect=lambda pid, **kw: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid, **kw: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=track_move), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", return_value=True), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # The two satellite VLC windows are positioned at final locations during Phase 4
        positioned_hwnds = {hwnd for hwnd, x, y, w, h in move_calls}
        assert {3030, 4040} <= positioned_hwnds

        # Nothing may be activated while the loading screen is up
        assert all(activate is False for activate in move_activates), \
            f"move_window must not activate in loading screen mode: {move_activates}"

        # core_hwnds contains the two satellite VLC windows plus Nau
        assert set(result.core_hwnds) == {3030, 4040, 2525}

    def test_passes_hide_windows_to_start_core_session(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        core_kwargs = {}

        def capture_core(**kwargs):
            core_kwargs.update(kwargs)
            _write_result(kwargs["result_file"], CORE_PIDS)

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=capture_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.hide_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", return_value=True), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        assert core_kwargs["hide_windows"] is True


class TestPhase4Reveal:
    """Phase 4 (hide_windows only): restore volume, play satellites, unpause Nau."""

    def _run_hidden(self, manifest_path, tmp_path, *, vlc_http_cmd, pid_to_hwnd=None, title_to_hwnd=None, topmost_calls=None):
        pid_map = pid_to_hwnd or {30: 3030, 40: 4040, NAU_PID: 2525, 50: 5050}
        title_map = title_to_hwnd or {"Fun Time": 5050, "Genau": 6060}
        topmost_tracker = (lambda h, v: topmost_calls.append((h, v))) if topmost_calls is not None else (lambda h, v: None)
        hide_calls = self._hide_calls = []

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=_fake_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=GENAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_nau", return_value=NAU_PID), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=_fake_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", side_effect=lambda pid, **kw: pid_map.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid, **kw: pid_map.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window_by_title", side_effect=lambda title, **kw: title_map.get(title, 0)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=topmost_tracker), \
             patch("fun_time.windows_bridge_sequencer.hide_window", side_effect=hide_calls.append), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", side_effect=vlc_http_cmd), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            return run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

    def test_restores_volume_and_plays_both_satellites(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = configparser.ConfigParser()
        m.optionxform = str
        m.read(str(manifest_path), encoding="utf-8")

        vlc_cmds: list[tuple] = []

        def track_vlc(port, cmd, password):
            vlc_cmds.append((port, cmd))
            return True

        self._run_hidden(manifest_path, tmp_path, vlc_http_cmd=track_vlc)

        portrait_port = int(m["vlc"]["vlc2_port"])
        landscape_port = int(m["vlc"]["vlc3_port"])

        # Volume restored and playback started on both satellites.
        assert (portrait_port, "volume&val=256") in vlc_cmds
        assert (landscape_port, "volume&val=256") in vlc_cmds
        assert (portrait_port, "pl_play") in vlc_cmds
        assert (landscape_port, "pl_play") in vlc_cmds

    def test_unpauses_nau_and_keeps_genau_parked(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = configparser.ConfigParser()
        m.optionxform = str
        m.read(str(manifest_path), encoding="utf-8")
        # Start all three flags paused, as seed_paused_states does
        for key in ("genau_paused_file", "audio_paused_file", "nau_paused_file"):
            flag = Path(m["commands"][key])
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("1", encoding="utf-8")

        self._run_hidden(manifest_path, tmp_path, vlc_http_cmd=lambda *a: True)

        # The reveal: Nau is unpaused so it starts playing when the loading
        # screen comes down; Genau and audio stay parked.
        assert Path(m["commands"]["nau_paused_file"]).read_text(encoding="utf-8").strip() == "0"
        assert Path(m["commands"]["genau_paused_file"]).read_text(encoding="utf-8").strip() == "1"
        assert Path(m["commands"]["audio_paused_file"]).read_text(encoding="utf-8").strip() == "1"

    def test_startup_window_state_topmost_policy_and_nau_only_visible(self, cfg_factory, tmp_path):
        """No z-order: every managed window gets its topmost flag from the shared
        policy for nau startup mode — where Nau floats topmost alongside the rest
        — and the nau startup mode hides the inactive slot-mate (Genau)."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        topmost_calls: list[tuple] = []
        self._run_hidden(
            manifest_path, tmp_path, vlc_http_cmd=lambda *a: True, topmost_calls=topmost_calls,
        )

        NAU_HWND, GENAU_HWND = 2525, 6060
        assert (NAU_HWND, True) in topmost_calls   # nau mode: Nau floats topmost
        assert (GENAU_HWND, True) in topmost_calls  # topmost even while hidden
        assert set(self._hide_calls) == {GENAU_HWND}
        assert NAU_HWND not in self._hide_calls

    def test_dashboard_found_by_title_gets_topmost(self, cfg_factory, tmp_path):
        """find_window_by_pid fails for the dashboard because the venv launcher
        PID differs from the Qt window's PID — Phase 4 must fall back to the
        exact title lookup ("Fun Time") to give the dashboard its topmost flag."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        DASH_HWND = 5050
        # No entry for the dashboard pid (50): pid lookup returns 0
        pid_to_hwnd = {30: 3030, 40: 4040, NAU_PID: 2525}
        title_to_hwnd = {"Fun Time": DASH_HWND, "Genau": 6060}

        topmost_calls: list[tuple] = []
        self._run_hidden(
            manifest_path, tmp_path, vlc_http_cmd=lambda *a: True,
            pid_to_hwnd=pid_to_hwnd, title_to_hwnd=title_to_hwnd, topmost_calls=topmost_calls,
        )

        assert (DASH_HWND, True) in topmost_calls


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

    def test_passes_placeholder_path_when_lazy_load_enabled(self, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        m = self._make_manifest_parser()
        m["random_favs_browser"]["lazy_load"] = "1"
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

        assert "placeholder_path" in launch_kwargs
        from fun_time.windows_bridge_random_favs_browser import tab_placeholder_path
        assert launch_kwargs["placeholder_path"] == tab_placeholder_path()

    def test_no_placeholder_path_when_lazy_load_disabled(self, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        m = self._make_manifest_parser()
        # lazy_load absent = disabled
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

        assert launch_kwargs.get("placeholder_path") is None
