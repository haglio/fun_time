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

import pytest
from PyQt6.QtWidgets import QApplication

from fun_time import win32


@pytest.fixture(autouse=True, scope="session")
def _qapp():
    """Ensure a QApplication instance exists for the test session."""
    app = QApplication.instance() or QApplication([])
    yield app


# The old "never open a socket to a live VLC" guard is gone with VLC itself: the
# satellites are native mpv players driven through per-test tmp command/status
# files (satellite_control), so a unit test can no longer reach the user's live
# session the way an unmocked vlc_actions HTTP call once could.


# The window wrappers in fun_time.win32 all funnel through a few user32 calls, and
# the same machine runs the user's live Fun Time.  So an unmocked window call in a
# unit test lands on THEIR windows: a test that reaches the real ``set_always_on_top``
# resolves the live "Nau"/"Genau" window by title and forces it on top — the test
# bleed behind "Nau pops on top during OmniPause" (it looked like a runtime/OmniPause
# bug for months because it WAS our code, run by a concurrent agent's test process).
# This is the window analog of _never_open_a_socket_to_vlc.
_MUTATING_USER32_CALLS = ("SetWindowPos", "SetForegroundWindow", "ShowWindow", "PostMessageW")


@pytest.fixture(autouse=True)
def _never_mutate_a_real_window(monkeypatch):
    """Neutralise the win32 calls that MOVE/topmost/activate/close a window, so no
    unit test can touch the user's live windows.

    Only the mutating primitives are stubbed; the readers (``GetWindowLongW``,
    ``EnumWindows``, the z-order walk) stay real, so ``is_window_topmost`` /
    ``find_window_*`` / ``iter_zorder`` still behave — resolving a live handle is
    harmless, and any attempt to then mutate it is inert.  Tests that assert on these
    calls patch ``fun_time.win32._user32`` themselves, which overrides this; the
    integration suite overrides it too, because it drives real native windows.
    """
    def _inert(*_args, **_kwargs):
        return 0

    for name in _MUTATING_USER32_CALLS:
        monkeypatch.setattr(win32._user32, name, _inert)


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
