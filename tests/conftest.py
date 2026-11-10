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
from fun_time.config import DEFAULT_CONFIG_PATH, load_config
from fun_time_vr import orchestrator as vr_orchestrator
from tests.headset_refusal import his_headset_refused
from tests.hosted_shows import an_origenerator_that_answers
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


_USER32_CALLS_THAT_CHANGE_A_WINDOW = (
    "SetWindowPos", "SetForegroundWindow", "ShowWindow", "PostMessageW", "BringWindowToTop",
    "SetActiveWindow",
)


@pytest.fixture(autouse=True)
def _never_change_a_window_of_his_live_fun_time(monkeypatch):
    def _inert(*_args, **_kwargs):
        return 0

    for name in _USER32_CALLS_THAT_CHANGE_A_WINDOW:
        monkeypatch.setattr(win32._user32, name, _inert)


@pytest.fixture(autouse=True)
def _never_switch_the_tab_he_is_looking_at(monkeypatch):
    def _refuse():
        raise RuntimeError("a unit test tried to reach the tabs of his own Chrome")

    monkeypatch.setattr(chrome_accessibility, "_Automation", _refuse)


@pytest.fixture(autouse=True)
def alert_sounds(monkeypatch):
    sounded: list[int] = []
    monkeypatch.setattr(winsound, "MessageBeep", sounded.append)
    return sounded


@pytest.fixture(autouse=True)
def _never_listen_to_his_desktop(monkeypatch):
    monkeypatch.setattr(win32_events.PressAndCaretWatch, "start", lambda _watch: None)


@pytest.fixture(autouse=True)
def _never_reach_the_real_headset(monkeypatch):
    with his_headset_refused(monkeypatch):
        yield


@pytest.fixture(autouse=True)
def _no_session_run_in_this_process_takes_his_loopback_port(monkeypatch):
    monkeypatch.setattr(windows_bridge_orchestrator, "serve_loopback", lambda **_kwargs: None)


_THE_HOLD_THE_ORCHESTRATOR_TIMES_WITHOUT_THE_LOOKUP = "CLOSING_SCREEN_READY_CEILING_S"
_THE_BUDGET_A_TESTS_OWN_WAITER_WOULD_STILL_SPEND = "POST_LOADING_RESOLVE_TIMEOUT_S"
_STARTUP_TIMEOUTS_OTHER_TESTS_PIN_AS_PRODUCTION_NUMBERS = (
    _THE_HOLD_THE_ORCHESTRATOR_TIMES_WITHOUT_THE_LOOKUP,
    _THE_BUDGET_A_TESTS_OWN_WAITER_WOULD_STILL_SPEND,
)


@pytest.fixture(autouse=True)
def _never_wait_out_a_window_no_test_opened(request, monkeypatch):
    if request.node.get_closest_marker("real_startup_waits"):
        return
    monkeypatch.setattr(windows_bridge_orchestrator, "wait_for_window_by_title",
                        lambda _title, **_kwargs: 0)
    monkeypatch.setattr(raising_the_loading_screen, "wait_for_window", lambda _find, _timeout_s: 0)
    for timeout in _STARTUP_TIMEOUTS_OTHER_TESTS_PIN_AS_PRODUCTION_NUMBERS:
        monkeypatch.setattr(windows_bridge_orchestrator, timeout, 0)


_SCREENS_OVER_EVERY_MONITOR = (loading_screen, transition_screen, closing_screen)
_STARTS_A_SCREEN = re.compile(
    r"-m\s+(" + "|".join(re.escape(screen.__name__) for screen in _SCREENS_OVER_EVERY_MONITOR)
    + r")\b")


@pytest.fixture(autouse=True)
def _never_start_a_real_loading_screen(monkeypatch):
    the_start_under_any_mocked_popen = _winapi.CreateProcess

    def refuse_a_screen(application_name, command_line, *rest):
        if _STARTS_A_SCREEN.search(command_line or ""):
            pytest.fail(f"a unit test started a real screen over every monitor: {command_line}")
        return the_start_under_any_mocked_popen(application_name, command_line, *rest)

    monkeypatch.setattr(_winapi, "CreateProcess", refuse_a_screen)


_WHERE_A_SESSION_LOOKS_UP_ITS_HEARTBEAT = (orchestrator, session_handoff, vr_orchestrator)


@pytest.fixture(autouse=True)
def _no_heartbeat_outlives_its_test_reading_its_tmp_path(monkeypatch):
    for looks_it_up in _WHERE_A_SESSION_LOOKS_UP_ITS_HEARTBEAT:
        monkeypatch.setattr(looks_it_up, "keep_the_transition_screen", lambda _state_dir: None)


@pytest.fixture(autouse=True)
def _never_act_as_if_on_the_hidden_desktop(monkeypatch):
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
    (tmp_path / "origenerator").mkdir(exist_ok=True)

    cfg: dict = {
        "paths": {
            "ahk_exe": str(tmp_path / "ahk.exe"),
            "python_exe": str(tmp_path / "python.exe"),
            "origenerator_dir": str(tmp_path / "origenerator"),
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
def hosted_origenerator(cfg_path: Path, monkeypatch) -> list[str]:
    return an_origenerator_that_answers(load_config(cfg_path), monkeypatch)


@pytest.fixture
def cfg_factory(tmp_path: Path, genaus_folder: Path):
    def factory(overrides: dict | None = None) -> Path:
        return _write_config(tmp_path, overrides)
    return factory
