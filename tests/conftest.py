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

from fun_time import win32, windows_bridge_orchestrator


@pytest.fixture(autouse=True, scope="session")
def _qapp():
    """Ensure a QApplication instance exists for the test session."""
    app = QApplication.instance() or QApplication([])
    yield app


# The window wrappers in fun_time.win32 all funnel through a few user32 calls, and
# the same machine runs the user's live Fun Time.  So an unmocked window call in a
# unit test lands on THEIR windows: a test that reaches the real ``set_always_on_top``
# resolves the live "Nau"/"Genau" window by title and forces it on top — the test
# bleed behind "Nau pops on top during OmniPause" (it looked like a runtime/OmniPause
# bug for months because it WAS our code, run by a concurrent agent's test process).
_MUTATING_USER32_CALLS = (
    "SetWindowPos", "SetForegroundWindow", "ShowWindow", "PostMessageW", "BringWindowToTop",
)


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


@pytest.fixture(autouse=True)
def _never_hold_the_live_loopback_port(monkeypatch):
    """Keep the orchestrator tests off the port a live session serves on.

    Every test that runs ``run_python_orchestrated_bridge`` reaches the real
    ``serve_loopback``, and a bound port is a bound port: for the length of the
    run this pytest — not the user — would own 8770.  A Fun Time opened meanwhile
    finds it busy, logs the warning, and comes up with no loopback server at
    all, so Tampermonkey stops auto-updating and the RFB tab pages never hear
    about OmniPause.  The integration suite needs no override: it runs the
    orchestrator as a subprocess, which never sees this patch, and gives that
    subprocess a ``loopback_port`` of its own to serve on.
    """
    monkeypatch.setattr(windows_bridge_orchestrator, "serve_loopback", lambda **_kwargs: None)


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
