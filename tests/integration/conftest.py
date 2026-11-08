from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, MutableMapping
from functools import partial
from pathlib import Path

import pytest
import pytest_timeout

from . import run_clock
from .hidden_desktop import REFUSED_EXIT_CODE, on_hidden_desktop, require_hidden_desktop
from .integration_support import (
    RootsLeftBehind,
    clear_run_roots,
    close_the_runs_osr2s,
    genau_where_this_machine_keeps_it,
    keep_what_the_run_logged,
    where_failed_runs_are_kept,
)
from .line_record import require_a_run_that_holds_the_machine
from .run_pulse import PULSE_ENV, Pulse
from .throwaway_chrome import ThrowawayChrome


def draw_real_windows_only_on_the_hidden_desktop(environ: MutableMapping[str, str] = os.environ,
                                                hidden: Callable[[], bool] = on_hidden_desktop) -> None:
    if sys.platform == "win32" and not hidden():
        return
    environ.pop("QT_QPA_PLATFORM", None)
    environ.pop("SDL_VIDEODRIVER", None)


draw_real_windows_only_on_the_hidden_desktop()


def refuse_a_run_that_may_not_start() -> None:
    if sys.platform != "win32":
        return
    try:
        require_hidden_desktop()
        require_a_run_that_holds_the_machine()
    except RuntimeError as refused:
        # pytest.exit rather than letting it propagate: an exception out of a
        # hook is reported as an INTERNALERROR traceback, which reads
        # as a broken harness instead of what it is — the run being invoked wrongly.
        pytest.exit(str(refused), returncode=REFUSED_EXIT_CODE)


_THE_RUNS_CLOCK = pytest.StashKey[tuple[run_clock.RunClock, float]]()
_THE_RUNS_PULSE = pytest.StashKey[Pulse]()


def pytest_configure(config):
    if on_hidden_desktop():
        clock = run_clock.RunClock()
        config.stash[_THE_RUNS_CLOCK] = (clock, time.monotonic())
        config.add_cleanup(partial(run_clock.keep_time_by, run_clock.WALL))
        run_clock.keep_time_by(clock)
        if told_where := os.environ.get(PULSE_ENV):
            pulse = Pulse(Path(told_where))
            config.stash[_THE_RUNS_PULSE] = pulse
            config.add_cleanup(pulse.stop)


def _say_how_much_of_the_run_was_its_own(clock: run_clock.RunClock, started: float) -> None:
    print(f"\n[integration] the run could run for {clock.seconds() / 60:.1f} of its "
          f"{(time.monotonic() - started) / 60:.1f} minutes; other work held the processors "
          "for the rest", file=sys.stderr, flush=True)


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_set_timer(item, settings):
    if settings.method != "thread":
        return None
    item.cancel_timeout = run_clock.watch_for_a_timeout(
        settings.timeout, partial(pytest_timeout.timeout_timer, item, settings))
    return True


def pytest_runtest_protocol(item, nextitem):
    if (pulse := item.config.stash.get(_THE_RUNS_PULSE, None)) is not None:
        pulse.on_test(item.nodeid)


def pytest_sessionstart(session):
    refuse_a_run_that_may_not_start()


def pytest_collection_modifyitems(session, config, items):
    refuse_a_run_that_may_not_start()


FAILURE_EVIDENCE = where_failed_runs_are_kept(Path(__file__).resolve().parents[2])


def pytest_sessionfinish(session, exitstatus):
    if (kept_time := session.config.stash.get(_THE_RUNS_CLOCK, None)) is not None:
        _say_how_much_of_the_run_was_its_own(*kept_time)
    if exitstatus != 0:
        kept = keep_what_the_run_logged(FAILURE_EVIDENCE)
        if kept is not None:
            print(f"\n[integration] what this failed run's sessions and satellites logged "
                  f"is in {kept}", file=sys.stderr, flush=True)
    try:
        clear_run_roots()
    except RootsLeftBehind as left_behind:
        print(f"\n[integration] {left_behind}", file=sys.stderr, flush=True)
        session.exitstatus = exitstatus or pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def _never_inherit_the_integration_flag():
    return


@pytest.fixture(autouse=True)
def _never_mutate_a_real_window():
    return


@pytest.fixture(autouse=True)
def _never_start_a_real_loading_screen():
    return


@pytest.fixture(autouse=True)
def _never_switch_a_real_browsers_tabs():
    return


@pytest.fixture(autouse=True)
def _never_listen_to_his_desktop():
    return


@pytest.fixture(autouse=True)
def _no_thread_outlives_its_test():
    return


@pytest.fixture(scope="session", autouse=True)
def _the_runs_udp_sinks_stay_bound_until_the_run_ends():
    yield
    close_the_runs_osr2s()


@pytest.fixture(scope="session", autouse=True)
def _genau_where_this_machine_keeps_it():
    with genau_where_this_machine_keeps_it():
        yield


@pytest.fixture
def chrome(tmp_path: Path):
    browser = ThrowawayChrome(tmp_path)
    try:
        yield browser
    finally:
        browser.close()
