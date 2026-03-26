from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from fun_time.config import load_config
from fun_time.windows_bridge_manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.windows_bridge_sequencer import (
    StartupResult,
    run_startup_sequence,
)
from fun_time.windows_bridge_monitors import MonitorInfo
from fun_time.windows_bridge_window_layout import WindowLayoutPlan, WindowRect


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
        ui_pids = {"dashboard_pid": 500, "robot_hand_pid": 600, "audio_pid": 700}

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
        assert result.robot_hand_pid == 600
        assert result.audio_pid == 700

        assert core_called["password"] == "testpw"
        assert ui_called["mfp_pid"] == 200

    def test_positions_windows_after_mfp_ready(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        core_pids = {"primary_pid": 10, "mfp_pid": 20, "portrait_pid": 30, "landscape_pid": 40}
        ui_pids = {"dashboard_pid": 50, "robot_hand_pid": 60, "audio_pid": 70}

        move_calls: list[tuple] = []
        topmost_calls: list[tuple] = []

        def track_move(hwnd, x, y, w, h):
            move_calls.append((hwnd, x, y, w, h))

        def track_topmost(hwnd, on_top):
            topmost_calls.append((hwnd, on_top))

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
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
        ui_pids = {"dashboard_pid": 50, "robot_hand_pid": 60, "audio_pid": 70}

        with patch("fun_time.windows_bridge_sequencer.start_core_session", side_effect=lambda **kw: _write_result(kw["result_file"], core_pids)), \
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
