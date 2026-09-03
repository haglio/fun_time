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

# The same sibling-checkout override the orchestrator applies at launch: in a
# worktree whose branch leans on unlanded genau/player_core changes, the suite
# must test against those checkouts — they are what the verification session
# runs — and without this the imports below resolve the primaries and a
# cross-repo branch cannot even collect.  A no-op in the primary and in CI,
# where no override file exists.
from fun_time.branch_session import apply_genau_dirs_to_sys_path

apply_genau_dirs_to_sys_path()

from fun_time import win32, windows_bridge_orchestrator
from fun_time.config import DEFAULT_CONFIG_PATH

# test_real_config_launchable is a check on THIS MACHINE's state — the
# git-ignored real config — not on the code.  Off the machine (CI, public
# checkouts) there is nothing to validate, and skipping put a permanent hole
# in the zero-skips rule; so off the machine the file is not collected at all.
if not DEFAULT_CONFIG_PATH.is_file():
    collect_ignore = ["test_real_config_launchable.py"]


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

    Every test that runs ``run_session`` reaches the real
    ``serve_loopback``, and a bound port is a bound port: for the length of the
    run this pytest — not the user — would own 8770.  A Fun Time opened meanwhile
    finds it busy, logs the warning, and comes up with no loopback server at
    all, so Tampermonkey stops auto-updating and the RFB tab pages never hear
    about OmniPause.  The integration suite needs no override: it runs the
    orchestrator as a subprocess, which never sees this patch, and gives that
    subprocess a ``loopback_port`` of its own to serve on.
    """
    monkeypatch.setattr(windows_bridge_orchestrator, "serve_loopback", lambda **_kwargs: None)


@pytest.fixture(autouse=True)
def _never_wait_out_a_window_no_test_opened(request, monkeypatch):
    """Answer the orchestrator's startup waits at once instead of spending them.

    A unit test mocks the startup that would have opened the session's windows,
    so every lookup the orchestrator makes can only time out — and it times out
    on the wall clock, because ``win32.wait_for_window_by_title`` polls with
    ``time.sleep``.  One startup pass is the loading cover plus five role
    resolutions, so CI paid 20s per test that ran one, and
    ``test_windows_bridge_orchestrator.py`` alone was 359s of it.

    The stubbed lookup answers 0, which is what the poll returned anyway; the
    tests that care which handle comes back patch this name themselves, which
    overrides the stub for the length of their ``with`` block.

    The two timeouts are set here rather than edited in the module because both
    are pinned as production numbers:
    ``TestTheFinishingPassFitsBehindTheCover`` adds the resolve budget up
    against the cover's staleness guard, and ``TestWaitForClosingScreen`` walks
    every way out of the closing-screen hold.  A test that is ABOUT the waiting
    marks itself ``real_startup_waits`` and gets the real function and the real
    numbers back.
    """
    if request.node.get_closest_marker("real_startup_waits"):
        return
    monkeypatch.setattr(
        windows_bridge_orchestrator,
        "wait_for_window_by_title",
        lambda _title, **_kwargs: 0,
    )
    monkeypatch.setattr(windows_bridge_orchestrator, "CLOSING_SCREEN_READY_TIMEOUT_S", 0)
    monkeypatch.setattr(windows_bridge_orchestrator, "POST_LOADING_RESOLVE_TIMEOUT_S", 0)


@pytest.fixture(autouse=True)
def _fresh_group_index_cache():
    """Start every test with an empty media-metadata group-index cache.

    ``_INDEX_CACHE`` is process-global and survives between tests; before this
    fixture, whether a test began clean depended on its author remembering a
    ``reset_group_index_cache()`` prelude (ten call sites, seven provably
    unnecessary), and a forgotten one made a failure depend on run order."""
    from fun_time.media_metadata import reset_group_index_cache

    reset_group_index_cache()


@pytest.fixture(autouse=True)
def _never_inherit_the_integration_flag(monkeypatch):
    """Strip ``FUN_TIME_RUN_INTEGRATION`` from every unit test's environment.

    The flag tells the production code it is running under the hidden-desktop
    integration harness, and it flips real-window branches all over the tree
    (skip the focus steal, suppress the unsuspend, no activation) — so a shell
    that still exports it, a developer mid-integration-debugging, would flip
    those branches under the whole unit suite.  Scrubbed once here rather than
    as a delenv prelude in every test that noticed (27 of them, before this).

    The tests that are ABOUT the integration branches ``monkeypatch.setenv``
    the flag back, which runs after this fixture and wins.  The integration
    suite overrides this fixture in its own conftest — there the flag is the
    point.
    """
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)


TMP_ROOT = Path(
    os.environ.get(
        "FUN_TIME_PYTEST_TMP_ROOT",
        str(Path(__file__).resolve().parent.parent / ".tmp-pytest-local"),
    )
).resolve()


@pytest.fixture()
def tmp_path() -> Path:
    """Replace pytest's builtin ``tmp_path`` with a checkout-local scratch dir.

    Each test gets ``.tmp-pytest-local/case_<uuid>``, removed in the finally —
    including on failure, so unlike pytest's own fixture there is no
    retained-last-3-runs debris to inspect afterwards.  Note the trade-offs:
    the scratch tree lives inside the checkout (git-ignored, but on a synced
    drive), and ``tmp_path_factory`` (and plugins built on it) still point at
    the system temp dir, so the two are not interchangeable.  Set
    ``FUN_TIME_PYTEST_TMP_ROOT`` to relocate it.
    """
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
            "primary_monitor": 1,
            "secondary_monitor": 2,
            "main_top_ratio": 0.727,
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
