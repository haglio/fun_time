from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch, MagicMock

from fun_time.config import load_config
from fun_time.manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.windows_bridge_sequencer import (
    StartupResult,
    run_startup_sequence,
    _maybe_launch_random_favs_browser,
    _position_mfp_window,

)
from fun_time.monitors import MonitorInfo
from fun_time.window_layout import (
    MonitorRect,
    WindowLayoutPlan,
    WindowRect,
)
from fun_time.config import LayoutConfig
from fun_time.startup_progress import NullProgress


FAKE_MONITORS = [
    MonitorInfo(x=0, y=0, width=2560, height=1392),
    MonitorInfo(x=2560, y=0, width=1440, height=3440),
]


def _make_manifest(cfg_factory, tmp_path):
    cfg = load_config(cfg_factory())
    manifest_path = write_windows_bridge_manifest(
        cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )
    return cfg, manifest_path


class TestRunStartupSequence:
    def test_calls_start_core_session_and_launch_ui_companions(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 100, "mfp_pid": 200, "portrait_pid": 300, "landscape_pid": 400}
        ui_pids = {"dashboard_pid": 500, "genau_pid": 600, "audio_pid": 700}

        core_called = {}
        ui_called = {}

        def fake_start_core(**kwargs):
            core_called.update(kwargs)
            # Write a fake result file
            _write_result(kwargs["result_file"], core_pids)

        def fake_launch_ui(**kwargs):
            ui_called.update(kwargs)
            _write_result(kwargs["result_file"], ui_pids)

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=fake_start_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=fake_launch_ui), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=99999), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=99999), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.primary_pid == 100
        assert result.mfp_pid == 200
        assert result.portrait_pid == 300
        assert result.landscape_pid == 400
        assert result.dashboard_pid == 500
        assert result.genau_pid == 600
        assert result.audio_pid == 700

        assert core_called["password"] == "testpw"
        assert ui_called["mfp_pid"] == 200

    def test_positions_windows_after_mfp_ready(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        move_calls: list[tuple] = []
        topmost_calls: list[tuple] = []

        def track_move(hwnd, x, y, w, h, **_kw):
            move_calls.append((hwnd, x, y, w, h))

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=track_move), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # Should have moved portrait, primary, landscape, mfp windows
        moved_hwnds = [c[0] for c in move_calls]
        assert 88888 in moved_hwnds  # MFP window was moved

        # Should have set topmost on core windows
        topmost_hwnds = [c[0] for c in topmost_calls if c[1]]
        assert len(topmost_hwnds) >= 4  # primary, portrait, landscape, mfp

    def test_returns_layout_plan(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.layout_plan is not None
        assert result.layout_plan.portrait.x == 2560
        assert result.layout_plan.dashboard.width > 0


def _write_result(result_file, values):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {k: str(v) for k, v in values.items()}
    path = Path(result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


# ---------------------------------------------------------------------------
# Regression tests for live bugs fixed in f1fe6ad and follow-ups
# ---------------------------------------------------------------------------

FAKE_LAYOUT_CFG = LayoutConfig(
    main_monitor=0,
    secondary_monitor=1,
    primary_top_ratio=0.48,
    landscape_width_ratio=0.35,
    mfp_width_ratio=0.5,
    mfp_height_ratio=0.5,
)

MAIN_RECT = MonitorRect(x=0, y=0, width=2560, height=1392)


class TestPositionMfpWindow:
    """Regression: MFP must use retry loop with delta correction (bug #6)."""

    def test_retries_with_delta_correction(self):
        target = WindowRect(x=500, y=100, width=240, height=395)
        move_calls: list[tuple] = []

        def track_move(hwnd, x, y, w, h, **_kw):
            move_calls.append((x, y, w, h))

        # Simulate position being off by 10px on every attempt — forces all 3 retries
        def fake_get_rect(hwnd):
            if not move_calls:
                return (510, 110, 240, 395)
            return (move_calls[-1][1] + 10, move_calls[-1][2] + 10, 240, 395)

        with patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=77), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=track_move), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", side_effect=fake_get_rect), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            _position_mfp_window(20, target, MAIN_RECT, FAKE_LAYOUT_CFG)

        # Should have retried (more than 1 move call)
        assert len(move_calls) >= 2
        # Later calls should have adjusted coordinates from the initial request
        assert move_calls[-1][1:3] != move_calls[0][1:3]

    def test_stops_when_position_is_accurate(self):
        target = WindowRect(x=500, y=100, width=240, height=395)
        with patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=77), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(500, 100, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            _position_mfp_window(20, target, MAIN_RECT, FAKE_LAYOUT_CFG)

        # Only 1 move needed when position is accurate on first try


class TestMaybeLaunchRandomFavsBrowser:
    """Regression: browser must launch (bug #3) and MFP must stay on top (bug #8/z-order)."""

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
        from fun_time.dashboard_layout import Size
        return compute_window_layout(
            main_monitor=MAIN_RECT,
            secondary_monitor=MonitorRect(x=2560, y=0, width=1440, height=3440),
            layout_config=FAKE_LAYOUT_CFG,
            mfp_size=Size(240, 395),
        )

    def test_skipped_when_disabled(self):
        """When disabled=0, no browser launch or window positioning happens."""
        m = self._make_manifest_parser(enabled="0")
        plan = self._fake_plan()
        move_calls: list[tuple] = []

        with patch("fun_time.windows_bridge_sequencer.move_window",
                    side_effect=lambda *a, **kw: move_calls.append(a)):
            rfb_hwnd = _maybe_launch_random_favs_browser(m, plan, mfp_pid=20)

        assert move_calls == []
        assert rfb_hwnd == 0

    def test_launches_and_positions_browser(self, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        m = self._make_manifest_parser()
        plan = self._fake_plan()
        browser_rect = plan.random_favs_browser

        launch_result = MagicMock(should_launch=True)

        with patch("fun_time.windows_bridge_sequencer._resolve_shortcut", return_value=("chrome.exe", "", "")), \
             patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_favs_browser", return_value=launch_result), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window") as mock_move, \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top") as mock_topmost, \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=77777), \
             patch("fun_time.windows_bridge_sequencer.activate_window") as mock_activate:
            rfb_hwnd = _maybe_launch_random_favs_browser(m, plan, mfp_pid=20)

        # Browser window should be positioned at the planned rect
        mock_move.assert_called_once_with(
            55555, browser_rect.x, browser_rect.y, browser_rect.width, browser_rect.height,
            activate=True,
        )
        # Should return the browser hwnd for topmost management
        assert rfb_hwnd == 55555

    def test_mfp_topmost_toggled_after_browser_launch(self, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        """Regression: MFP z-order must be restored above browser by toggling topmost off/on."""
        m = self._make_manifest_parser()
        plan = self._fake_plan()
        launch_result = MagicMock(should_launch=True)

        topmost_calls: list[tuple] = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        with patch("fun_time.windows_bridge_sequencer._resolve_shortcut", return_value=("chrome.exe", "", "")), \
             patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_favs_browser", return_value=launch_result), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=77777), \
             patch("fun_time.windows_bridge_sequencer.activate_window") as mock_activate:
            _maybe_launch_random_favs_browser(m, plan, mfp_pid=20)

        # Browser should be set topmost so clicking it raises above MFP/Dashboard
        assert (55555, True) in topmost_calls

        # MFP should be toggled: first False (clear), then True (re-set)
        # so it starts above the browser in the topmost z-band
        mfp_calls = [(h, t) for h, t in topmost_calls if h == 77777]
        assert mfp_calls == [(77777, False), (77777, True)]

        # MFP should be activated last
        mock_activate.assert_called_once_with(77777)

    def test_skips_mfp_topmost_when_hide_windows(self):
        """During loading screen (hide_windows=True), the browser launch must
        NOT set MFP topmost — doing so makes MFP punch through the overlay.
        Phase 4 handles z-order after the loading screen closes.
        """
        m = self._make_manifest_parser()
        plan = self._fake_plan()
        launch_result = MagicMock(should_launch=True)

        topmost_calls: list[tuple] = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        with patch("fun_time.windows_bridge_sequencer._resolve_shortcut", return_value=("chrome.exe", "", "")), \
             patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_favs_browser", return_value=launch_result), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=77777), \
             patch("fun_time.windows_bridge_sequencer.activate_window"):
            _maybe_launch_random_favs_browser(m, plan, mfp_pid=20, hide_windows=True)

        # Neither MFP nor RFB must be set topmost during loading screen —
        # doing so punches through the overlay.  Phase 4 handles z-order.
        mfp_topmost_calls = [(h, t) for h, t in topmost_calls if h == 77777 and t is True]
        assert mfp_topmost_calls == [], (
            f"MFP was set topmost during hide_windows mode: {topmost_calls}"
        )
        rfb_topmost_calls = [(h, t) for h, t in topmost_calls if h == 55555 and t is True]
        assert rfb_topmost_calls == [], (
            f"RFB was set topmost during hide_windows mode: {topmost_calls}"
        )

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

        with patch("fun_time.windows_bridge_sequencer._resolve_shortcut", return_value=("chrome.exe", "", "")), \
             patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_favs_browser", side_effect=capture_launch), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=77777), \
             patch("fun_time.windows_bridge_sequencer.activate_window"):
            _maybe_launch_random_favs_browser(m, plan, mfp_pid=20)

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

        with patch("fun_time.windows_bridge_sequencer._resolve_shortcut", return_value=("chrome.exe", "", "")), \
             patch("fun_time.windows_bridge_sequencer._get_chrome_window_hwnds", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_favs_browser", side_effect=capture_launch), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=77777), \
             patch("fun_time.windows_bridge_sequencer.activate_window"):
            _maybe_launch_random_favs_browser(m, plan, mfp_pid=20)

        assert launch_kwargs.get("placeholder_path") is None


class TestTopmostOnAllCoreWindows:
    """Regression: topmost must be set on all 4 core windows, not just some (bug #5)."""

    def test_topmost_set_on_primary_portrait_landscape_mfp(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        topmost_calls: list[tuple] = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        # Map each PID to a unique hwnd so we can verify all 4 get topmost
        pid_to_hwnd = {10: 1010, 20: 2020, 30: 3030, 40: 4040}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=2020), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # All 4 core hwnds should have been set topmost (True)
        hwnds_set_topmost = {h for h, on in topmost_calls if on}
        assert 1010 in hwnds_set_topmost, "primary not set topmost"
        assert 2020 in hwnds_set_topmost, "mfp not set topmost"
        assert 3030 in hwnds_set_topmost, "portrait not set topmost"
        assert 4040 in hwnds_set_topmost, "landscape not set topmost"


class TestNoActivateWindowDuringIntegration:
    """During integration tests, activate_window must be skipped to avoid focus stealing."""

    def test_skips_activate_window_in_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        pid_to_hwnd = {10: 1010, 20: 2020, 30: 3030, 40: 4040}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=2020), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window") as mock_activate, \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # activate_window should NOT have been called at all during integration
        assert mock_activate.call_count == 0, \
            f"activate_window called during integration: {mock_activate.call_args_list}"

    def test_activates_windows_outside_integration_mode(self, cfg_factory, tmp_path, monkeypatch):
        monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        pid_to_hwnd = {10: 1010, 20: 2020, 30: 3030, 40: 4040}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=2020), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window") as mock_activate, \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        # activate_window SHOULD have been called on core windows in normal mode
        core_hwnds = {1010, 2020, 3030, 4040}
        activated_hwnds = {c.args[0] for c in mock_activate.call_args_list}
        assert core_hwnds & activated_hwnds, "activate_window was not called on any core windows in normal mode"


class TestProgressReporting:
    """run_startup_sequence reports progress via the callback."""

    def test_hide_windows_advance_count_matches_total_steps(self, cfg_factory, tmp_path):
        """In hide_windows mode the number of advance() calls must equal
        _STARTUP_PROGRESS_STEPS so the progress bar reaches 100%.
        """
        from fun_time.windows_bridge_orchestrator import _STARTUP_PROGRESS_STEPS

        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        advance_messages: list[str] = []

        class TrackingProgress:
            def advance(self, message: str) -> None:
                advance_messages.append(message)
            def finish(self) -> None:
                pass

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
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

        assert len(advance_messages) == _STARTUP_PROGRESS_STEPS, (
            f"hide_windows fires {len(advance_messages)} steps "
            f"but _STARTUP_PROGRESS_STEPS={_STARTUP_PROGRESS_STEPS} — "
            f"progress bar reaches {100 * len(advance_messages) // _STARTUP_PROGRESS_STEPS}%"
        )

    def test_advance_called_for_each_startup_step(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        advance_messages: list[str] = []

        class TrackingProgress:
            def advance(self, message: str) -> None:
                advance_messages.append(message)
            def finish(self) -> None:
                pass

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=TrackingProgress(),
            )

        # Should have progress steps for each major milestone
        assert len(advance_messages) >= 7, f"Only {len(advance_messages)} progress steps reported"
        # Verify key milestones are reported
        messages_text = " ".join(advance_messages)
        assert "services" in messages_text.lower()
        assert "window" in messages_text.lower()
        assert "companion" in messages_text.lower()

    def test_null_progress_accepted_silently(self, cfg_factory, tmp_path):
        """NullProgress should work as a no-op."""
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=NullProgress(),
            )

        assert result.primary_pid == 10


class TestLoadingScreenStartup:
    """When hide_windows=True, positioning is deferred until after UI companions launch."""

    def test_defers_positioning_and_collects_hwnds(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        pid_to_hwnd = {10: 1010, 20: 2020, 30: 3030, 40: 4040}
        move_calls: list[tuple] = []

        def track_move(hwnd, x, y, w, h, **kw):
            move_calls.append((hwnd, x, y, w, h))

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", side_effect=lambda pid, **kw: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window", side_effect=track_move), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window") as mock_activate, \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            result = run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # Windows should be positioned at final locations during Phase 4
        positioned_hwnds = {hwnd for hwnd, x, y, w, h in move_calls}
        assert 1010 in positioned_hwnds or 2020 in positioned_hwnds, "Core windows should be positioned"

        # activate_window should not be called in loading screen mode
        mock_activate.assert_not_called()

        # core_hwnds should contain the window handles
        assert set(result.core_hwnds) == {1010, 2020, 3030, 4040}


    def test_passes_hide_windows_to_start_core_session(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        core_kwargs = {}

        def capture_core(**kwargs):
            core_kwargs.update(kwargs)
            _write_result(kwargs["result_file"], core_pids)

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=capture_core), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=600), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        assert core_kwargs["hide_windows"] is True


class TestPhase4GenauAlwaysInactive:
    """genau_active_at_startup is always False — all VLC ports play, no genau unpause."""

    def test_all_vlc_ports_get_pl_play(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        m = configparser.ConfigParser()
        m.optionxform = str
        m.read(str(manifest_path), encoding="utf-8")

        vlc_cmds: list[tuple] = []

        def track_vlc(port, cmd, password):
            vlc_cmds.append((port, cmd))
            return True

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=60), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", side_effect=track_vlc), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        primary_port = int(m["vlc"]["primary_vlc_port"])
        portrait_port = int(m["vlc"]["vlc2_port"])
        landscape_port = int(m["vlc"]["vlc3_port"])

        # All VLC ports should get pl_play (genau never active at startup)
        assert (primary_port, "pl_play") in vlc_cmds
        assert (portrait_port, "pl_play") in vlc_cmds
        assert (landscape_port, "pl_play") in vlc_cmds
        # All three should get volume restored
        assert (primary_port, "volume&val=256") in vlc_cmds

    def test_genau_stays_paused_at_startup(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        m = configparser.ConfigParser()
        m.optionxform = str
        m.read(str(manifest_path), encoding="utf-8")
        Path(m["commands"]["genau_mode_file"]).parent.mkdir(parents=True, exist_ok=True)
        # Start paused (as seed_genau_state does)
        Path(m["commands"]["genau_paused_file"]).write_text("1", encoding="utf-8")
        Path(m["commands"]["audio_paused_file"]).write_text("1", encoding="utf-8")

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=60), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", return_value=True), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # Pause files must NOT be overwritten — genau is never active at startup
        assert Path(m["commands"]["genau_paused_file"]).read_text(encoding="utf-8").strip() == "1"
        assert Path(m["commands"]["audio_paused_file"]).read_text(encoding="utf-8").strip() == "1"

    def test_genau_not_topmost_or_activated_at_startup(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        topmost_calls: list[tuple] = []
        activate_calls: list[int] = []
        rh_hwnd = 77777
        pid_to_hwnd = {10: 1010, 20: 2020, 30: 3030, 40: 4040, 50: 5050, 60: rh_hwnd}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=60), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", side_effect=lambda pid, **kw: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))), \
             patch("fun_time.windows_bridge_sequencer.activate_window", side_effect=lambda h: activate_calls.append(h)), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", return_value=True), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # Genau should NOT be set topmost (it's not active at startup)
        genau_topmost = [(h, v) for h, v in topmost_calls if h == rh_hwnd and v]
        assert genau_topmost == [], "Genau should not be set topmost at startup"
        # Genau should NOT be activated
        assert rh_hwnd not in activate_calls, "Genau should not be activated at startup"

    def test_dashboard_is_last_topmost_in_phase4(self, cfg_factory, tmp_path):
        """Dashboard must be the last topmost=True call so it's above Primary VLC.

        A previous fix incorrectly re-asserted Primary as the last topmost call
        (to put it above Genau), which put Primary above Dashboard.  Genau never
        sets itself topmost, so it's already behind all topmost windows — no
        explicit demotion is needed.
        """
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        topmost_calls: list[tuple] = []
        DASH_HWND = 5050
        pid_to_hwnd = {10: 1010, 20: 2020, 30: 3030, 40: 4040, 50: DASH_HWND, 60: 77777}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=60), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", side_effect=lambda pid, **kw: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid: pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=lambda h, v: topmost_calls.append((h, v))), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", return_value=True), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # Dashboard must be the last topmost=True call — it sits on top of
        # everything.  Primary VLC must NOT be re-asserted after Dashboard.
        last_topmost_true = [(h, v) for h, v in topmost_calls if v][-1]
        assert last_topmost_true[0] == DASH_HWND, (
            f"Dashboard (hwnd={DASH_HWND}) must be the last topmost call, "
            f"but hwnd={last_topmost_true[0]} was"
        )

    def test_no_auto_udp_sent_at_startup(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        udp_sent: list[tuple[bytes, tuple[str, int]]] = []

        class FakeSocket:
            def sendto(self, data, addr):
                udp_sent.append((data, addr))
            def close(self):
                pass

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=60), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", return_value=True), \
             patch("fun_time.windows_bridge_sequencer.socket.socket", return_value=FakeSocket()), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # No AUTO UDP should be sent (genau never active at startup)
        auto_msgs = [msg for msg in udp_sent if b"AUTO" in msg[0]]
        assert auto_msgs == [], "No AUTO UDP should be sent at startup"


class TestDashboardZOrderOnSlowStartup:
    """Bug: Dashboard z-order correction in Phase 4 uses find_window_by_pid (one-shot).

    If the Dashboard subprocess hasn't created its window yet, find_window_by_pid
    returns 0 and the z-order toggle is silently skipped — leaving Dashboard
    below RFB.  The fix: wait for the Dashboard window (like we wait for MFP).
    """

    def test_dashboard_topmost_toggled_even_when_window_appears_late(self, cfg_factory, tmp_path):
        """When find_window_by_pid returns 0 for the Dashboard (window not yet
        created), the sequencer must wait for it and still apply the z-order
        correction (toggle off/on topmost).
        """
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        DASH_HWND = 5050
        # find_window_by_pid returns 0 for dashboard (simulates slow subprocess)
        core_pid_to_hwnd = {10: 1010, 20: 2020, 30: 3030, 40: 4040}
        # wait_for_window returns the hwnd for both MFP and dashboard
        wait_pid_to_hwnd = {20: 2020, 50: DASH_HWND}

        topmost_calls: list[tuple] = []

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", return_value=60), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", side_effect=lambda pid, **kw: wait_pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", side_effect=lambda pid: core_pid_to_hwnd.get(pid, 0)), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top", side_effect=track_topmost), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.vlc_http_cmd", return_value=True), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        # Dashboard must be toggled off then on — even though find_window_by_pid
        # returned 0 — because the sequencer waited for the window.
        dash_calls = [(h, v) for h, v in topmost_calls if h == DASH_HWND]
        assert dash_calls == [(DASH_HWND, False), (DASH_HWND, True)], (
            f"Dashboard z-order correction was skipped or wrong: {dash_calls}"
        )


class TestGenauLaunchReceivesCommandFiles:
    """Genau must receive --command-file and --paused-file from the manifest."""

    def test_launch_genau_receives_manifest_file_paths(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "genau_pid": 60, "audio_pid": 70}

        genau_kwargs = {}

        def capture_launch_genau(**kwargs):
            genau_kwargs.update(kwargs)
            return 60

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
             patch("fun_time.windows_bridge_sequencer.launch_genau", side_effect=capture_launch_genau), \
             patch("fun_time.windows_bridge_sequencer.launch_ui_companions", side_effect=lambda **kw: _write_result(kw["result_file"], ui_pids)), \
             patch("fun_time.windows_bridge_sequencer.enumerate_monitors", return_value=FAKE_MONITORS), \
             patch("fun_time.windows_bridge_sequencer.wait_for_window", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.find_window_by_pid", return_value=88888), \
             patch("fun_time.windows_bridge_sequencer.get_window_rect", return_value=(0, 0, 240, 395)), \
             patch("fun_time.windows_bridge_sequencer.move_window"), \
             patch("fun_time.windows_bridge_sequencer.set_always_on_top"), \
             patch("fun_time.windows_bridge_sequencer.activate_window"), \
             patch("fun_time.windows_bridge_sequencer.time") as mock_time:
            mock_time.sleep = lambda _: None
            mock_time.monotonic = MagicMock(return_value=0)

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert "command_file" in genau_kwargs, "launch_genau must receive command_file"
        assert "paused_file" in genau_kwargs, "launch_genau must receive paused_file"
        assert genau_kwargs["command_file"] == str(cfg.genau_cmd_file)
        assert genau_kwargs["paused_file"] == str(cfg.genau_paused_file)
