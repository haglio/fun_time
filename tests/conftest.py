"""Shared pytest fixtures for Fun Time tests."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

# Render Qt offscreen for the whole unit suite. Agents run these GUI tests on every
# commit; without this, each test that shows a widget throws a real window onto the
# screen for a few milliseconds, so a run flashes a burst of windows across the
# monitors. Must be set before any QApplication is created (the _qapp fixture below).
# setdefault lets a developer override it to watch a test on a real display, and lets
# tests/integration/conftest.py restore the native platform — the integration tests
# inspect real windows and need real HWNDs, which the offscreen platform cannot give.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import http.client

import pytest
from PyQt6.QtWidgets import QApplication

from fun_time import vlc_actions


@pytest.fixture(autouse=True, scope="session")
def _qapp():
    """Ensure a QApplication instance exists for the test session."""
    app = QApplication.instance() or QApplication([])
    yield app


_REAL_HTTP_CONNECTION = http.client.HTTPConnection
_real_get_pooled_conn = vlc_actions._get_pooled_conn


@pytest.fixture(autouse=True)
def _never_open_a_socket_to_vlc(monkeypatch):
    """A unit test must never reach a real VLC.

    The suite runs on the same machine as the user's Fun Time, whose two
    satellites listen on the production HTTP ports.  An unmocked call lands on
    THEM: `ensure_playback_state` answers a paused VLC with `pl_pause`, and
    `pl_pause` toggles — so a background test run starts the user's video
    playing in the middle of their own OmniPause.

    Tests that exercise the HTTP layer itself substitute their own
    `HTTPConnection`; they are let through, because then no socket is opened.
    """
    def _guard(port: int):
        if vlc_actions.http.client.HTTPConnection is _REAL_HTTP_CONNECTION:
            raise AssertionError(
                f"a unit test tried to open a real socket to VLC on port {port} — "
                "mock the vlc_actions function this call goes through"
            )
        return _real_get_pooled_conn(port)

    monkeypatch.setattr(vlc_actions, "_get_pooled_conn", _guard)


TMP_ROOT = Path(
    os.environ.get(
        "FUN_TIME_PYTEST_TMP_ROOT",
        str(Path(__file__).resolve().parent.parent / ".tmp-pytest-local"),
    )
).resolve()


@pytest.fixture()
def tmp_path() -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = (TMP_ROOT / f"case_{uuid.uuid4().hex}").resolve()
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True, scope="session")
def _cleanup_tmp_root():
    """Remove TMP_ROOT after the session if it exists and is empty."""
    yield
    try:
        if TMP_ROOT.is_dir() and not any(TMP_ROOT.iterdir()):
            TMP_ROOT.rmdir()
    except OSError:
        pass


def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    """Write a minimal valid config JSON to tmp_path and return the path."""
    # Create stub directories / files that config validation expects.
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "clips").mkdir(exist_ok=True)
    (tmp_path / "audio").mkdir(exist_ok=True)
    (tmp_path / "videos" / "videos" / "portrait").mkdir(parents=True, exist_ok=True)
    (tmp_path / "videos" / "videos" / "landscape").mkdir(parents=True, exist_ok=True)
    (tmp_path / "weird").mkdir(exist_ok=True)
    (tmp_path / "videos" / "videos" / "nau_library").mkdir(parents=True, exist_ok=True)

    cfg: dict = {
        "paths": {
            "vlc_exe": str(tmp_path / "vlc.exe"),
            "ahk_exe": str(tmp_path / "ahk.exe"),
            "python_exe": str(tmp_path / "python.exe"),
            "nau_library_dirs": [str(tmp_path / "videos" / "videos" / "nau_library")],
            "portrait_dir": str(tmp_path / "videos" / "videos" / "portrait"),
            "landscape_dir": str(tmp_path / "videos" / "videos" / "landscape"),
            "weird_dir": str(tmp_path / "weird"),
            "clips_dir": str(tmp_path / "clips"),
            "audio_dir": str(tmp_path / "audio"),
            "favs_file": str(tmp_path / "favs.csv"),
            "state_dir": str(tmp_path / "state"),
        },
        "vlc": {
            "vlc2_http_port": 8091,
            "vlc3_http_port": 8092,
        },
        "layout": {
            "main_monitor": 1,
            "secondary_monitor": 2,
            "primary_top_ratio": 0.727,
            "landscape_width_ratio": 0.666,
        },
        "genau": {
            "shuffle_on_load": True,
            "beats_per_loop": 1.0,
            "clip_cache_size": 2,
            "render_batch": 6,
            "bpm_smoothing": 0.14,
            "sync_strength": 0.35,
            "udp_host": "127.0.0.1",
            "udp_port": 50555,
            "notify_host": "127.0.0.1",
            "notify_port": 50556,
            "status_hide_ms": 1200,
            "resize_debounce_ms": 120,
        },
        "audio_companion": {
            "host": "127.0.0.1",
            "port": 50556,
        },
    }

    if overrides:
        _deep_merge(cfg, overrides)

    config_path = tmp_path / "fun_time_config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    return config_path


def _deep_merge(base: dict, override: dict) -> None:
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


@pytest.fixture()
def cfg_path(tmp_path: Path) -> Path:
    """Return path to a written minimal valid config file."""
    return _write_config(tmp_path)


@pytest.fixture()
def cfg_factory(tmp_path: Path):
    """Return a factory that writes a config with optional overrides."""
    def factory(overrides: dict | None = None) -> Path:
        return _write_config(tmp_path, overrides)
    return factory
