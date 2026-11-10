from __future__ import annotations

import _winapi
import gc
import json
import os
import re
import sys
import winsound
from pathlib import Path

import pytest

from fun_time import (
    chrome_accessibility,
    closing_screen,
    library_browser,
    loading_screen,
    orchestrator,
    raising_the_loading_screen,
    session_handoff,
    transition_screen,
    win32,
    win32_events,
    windows_bridge_orchestrator,
)
from fun_time.config import DEFAULT_CONFIG_PATH
from fun_time.media_metadata import SESSION_INDEXES
from fun_time_vr import orchestrator as vr_orchestrator
from fun_time_vr import vr_runtime, windows_sound
from tests.logging_state import logging_given_back
from tests.made_up_genau import made_up_genau, point_the_shared_settings_at
from tests.process_state import the_guard_for
from tests.qt_application import application
from tests.scratch import scratch_dir, scratch_root_of
from tests.thread_state import no_thread_outlives_this

# test_real_config_launchable is a check on THIS MACHINE's state — the
# git-ignored real config — not on the code.  Off the machine (CI, public
# checkouts) there is nothing to validate, and skipping put a permanent gap
# in the zero-skips rule; so off the machine the file is not collected at all.
if not DEFAULT_CONFIG_PATH.is_file():
    collect_ignore = ["test_real_config_launchable.py"]


@pytest.fixture(autouse=True, scope="session")
def _qapp():
    with application([]) as app:
        yield app


_MUTATING_USER32_CALLS = (
    "SetWindowPos", "SetForegroundWindow", "ShowWindow", "PostMessageW", "BringWindowToTop",
    "SetActiveWindow",
)


@pytest.fixture(autouse=True)
def _never_mutate_a_real_window(monkeypatch):
    def _inert(*_args, **_kwargs):
        return 0

    for name in _MUTATING_USER32_CALLS:
        monkeypatch.setattr(win32._user32, name, _inert)


@pytest.fixture(autouse=True)
def _never_switch_a_real_browsers_tabs(monkeypatch):
    def _refuse():
        raise RuntimeError("a unit test tried to reach a real browser's tabs")

    monkeypatch.setattr(chrome_accessibility, "_Automation", _refuse)


@pytest.fixture(autouse=True)
def alert_sounds(monkeypatch):
    sounded: list[int] = []
    monkeypatch.setattr(winsound, "MessageBeep", sounded.append)
    return sounded


@pytest.fixture(autouse=True)
def _never_listen_to_his_desktop(monkeypatch):
    monkeypatch.setattr(win32_events.PressAndCaretWatch, "start", lambda _watch: None)


_WHAT_REACHES_HIS_HEADSET = {
    (vr_runtime, "probe"): "asked the VR runtime for a headset",
    (vr_runtime, "start_runtime"): "started the VR runtime",
    (vr_runtime, "stop_runtime"): "stopped the VR runtime",
    (windows_sound, "make_default"): "changed Windows' default sound device",
}


@pytest.fixture(autouse=True)
def _never_reach_the_real_headset(monkeypatch):
    reached = []

    def refuse(what):
        def refused(*_args, **_kwargs):
            reached.append(what)
            raise RuntimeError(f"a unit test {what}")
        return refused

    for (module, name), what in _WHAT_REACHES_HIS_HEADSET.items():
        monkeypatch.setattr(module, name, refuse(what))
    yield
    if reached:
        pytest.fail(f"a unit test reached his real headset: {', '.join(reached)}")


@pytest.fixture(autouse=True)
def _never_hold_the_live_loopback_port(monkeypatch):
    monkeypatch.setattr(windows_bridge_orchestrator, "serve_loopback", lambda **_kwargs: None)


@pytest.fixture(autouse=True)
def _never_wait_out_a_window_no_test_opened(request, monkeypatch):
    if request.node.get_closest_marker("real_startup_waits"):
        return
    monkeypatch.setattr(windows_bridge_orchestrator, "wait_for_window_by_title",
                        lambda _title, **_kwargs: 0)
    monkeypatch.setattr(raising_the_loading_screen, "wait_for_window", lambda _find, _timeout_s: 0)
    monkeypatch.setattr(windows_bridge_orchestrator, "CLOSING_SCREEN_READY_CEILING_S", 0)
    monkeypatch.setattr(windows_bridge_orchestrator, "POST_LOADING_RESOLVE_TIMEOUT_S", 0)


_SCREENS_OVER_EVERY_MONITOR = (loading_screen, transition_screen, closing_screen)
_STARTS_A_SCREEN = re.compile(
    r"-m\s+(" + "|".join(re.escape(screen.__name__) for screen in _SCREENS_OVER_EVERY_MONITOR)
    + r")\b")


@pytest.fixture(autouse=True)
def _never_start_a_real_loading_screen(monkeypatch):
    create_process = _winapi.CreateProcess

    def refuse_a_screen(application_name, command_line, *rest):
        if _STARTS_A_SCREEN.search(command_line or ""):
            pytest.fail(f"a unit test started a real screen over every monitor: {command_line}")
        return create_process(application_name, command_line, *rest)

    monkeypatch.setattr(_winapi, "CreateProcess", refuse_a_screen)


@pytest.fixture(autouse=True)
def _never_leave_a_loading_screen_heartbeat_running(monkeypatch):
    for module in (orchestrator, session_handoff, vr_orchestrator):
        monkeypatch.setattr(module, "keep_the_transition_screen", lambda _state_dir: None)


@pytest.fixture(autouse=True)
def _fresh_group_index_cache():
    SESSION_INDEXES.clear()


@pytest.fixture(autouse=True)
def _never_inherit_the_integration_flag(monkeypatch):
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)


@pytest.fixture(autouse=True)
def _runs_record_their_place_in_line_away_from_the_machine(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("FUN_TIME_LINE_RECORDS", str(tmp_path_factory.getbasetemp() / "line_records"))


@pytest.fixture
def unmuted(monkeypatch):
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO")


@pytest.fixture
def no_still_is_extracted_from_the_made_up_videos(monkeypatch):
    monkeypatch.setattr(library_browser, "thumbnail_for", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _logging_is_given_back():
    with logging_given_back():
        yield


@pytest.fixture(autouse=True)
def _no_thread_outlives_its_test():
    with no_thread_outlives_this():
        yield


@pytest.fixture(autouse=True)
def _no_process_outlives_its_test(request):
    with the_guard_for(request.path):
        yield


def pytest_configure(config):
    gc.disable()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    try:
        return (yield)
    finally:
        gc.collect(0 if nextitem is not None and nextitem.path == item.path else 2)


_THE_RUN = pytest.StashKey[pytest.Session]()


def pytest_sessionstart(session):
    session.config.stash[_THE_RUN] = session


@pytest.hookimpl(wrapper=True)
def pytest_unconfigure(config):
    yield
    if (run := config.stash.get(_THE_RUN, None)) is not None:
        _end_the_process_before_its_libraries_clean_up(int(run.exitstatus))


def _end_the_process_before_its_libraries_clean_up(exit_code: int) -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    _winapi.TerminateProcess(_winapi.GetCurrentProcess(), exit_code)


_EVERY_WORKERS_TMP_ROOT = Path(
    os.environ.get(
        "FUN_TIME_PYTEST_TMP_ROOT",
        str(Path(__file__).resolve().parent.parent / ".tmp-pytest-local"),
    )
).resolve()
TMP_ROOT = scratch_root_of(_EVERY_WORKERS_TMP_ROOT, os.environ)


@pytest.fixture
def tmp_path() -> Path:
    with scratch_dir(TMP_ROOT) as path:
        yield path


@pytest.fixture(autouse=True, scope="session")
def _cleanup_tmp_root():
    yield
    for root in (TMP_ROOT, _EVERY_WORKERS_TMP_ROOT):
        try:
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass


def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "videos" / "videos" / "main_player_library").mkdir(parents=True, exist_ok=True)

    cfg: dict = {
        "paths": {
            "ahk_exe": str(tmp_path / "ahk.exe"),
            "python_exe": str(tmp_path / "python.exe"),
            "main_player_library_dirs": [str(tmp_path / "videos" / "videos" / "main_player_library")],
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


@pytest.fixture
def genaus_folder(tmp_path: Path, monkeypatch) -> Path:
    folder = made_up_genau(tmp_path)
    point_the_shared_settings_at(monkeypatch, folder)
    return folder


@pytest.fixture
def cfg_path(tmp_path: Path, genaus_folder: Path) -> Path:
    return _write_config(tmp_path)


@pytest.fixture
def cfg_factory(tmp_path: Path, genaus_folder: Path):
    def factory(overrides: dict | None = None) -> Path:
        return _write_config(tmp_path, overrides)
    return factory
