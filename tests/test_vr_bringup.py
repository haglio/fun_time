from __future__ import annotations

import subprocess
import sys

from fun_time_vr.bringup import open_vr_session


class _DeviceNotReady(Exception):
    pass


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _open(attempts, clock, *, timeout_s=6.0, retry_s=2.0):
    def next_attempt():
        return attempts.pop(0)()

    return open_vr_session(
        next_attempt,
        device_not_ready=_DeviceNotReady,
        timeout_s=timeout_s,
        retry_s=retry_s,
        clock=clock,
    )


def _raises(exc):
    def attempt():
        raise exc
    return attempt


def test_a_session_that_opens_first_time_is_handed_straight_back():
    clock = _Clock()

    opened = _open([lambda: "session"], clock)

    assert opened.session == "session"
    assert opened.message == ""
    assert clock.slept == []


def test_a_cold_runtime_is_waited_out():
    clock = _Clock()

    opened = _open([_raises(_DeviceNotReady()), _raises(_DeviceNotReady()),
                    lambda: "session"], clock)

    assert opened.session == "session"
    assert clock.slept == [2.0, 2.0]


def test_a_runtime_that_never_comes_up_gives_up_at_the_deadline():
    clock = _Clock()

    opened = _open([_raises(_DeviceNotReady()) for _ in range(10)], clock)

    assert opened.session is None
    assert "graphics device never became ready" in opened.message
    assert sum(clock.slept) <= 6.0


def test_any_other_failure_is_reported_at_once():
    clock = _Clock()

    opened = _open([_raises(RuntimeError("no runtime"))], clock)

    assert opened.session is None
    assert "could not open a session" in opened.message
    assert "no runtime" in opened.message
    assert clock.slept == []


def test_the_deadline_is_measured_from_the_first_attempt():
    clock = _Clock()
    tried: list[float] = []

    def never_ready():
        tried.append(clock.now())
        raise _DeviceNotReady()

    open_vr_session(
        never_ready,
        device_not_ready=_DeviceNotReady,
        timeout_s=6.0,
        retry_s=2.0,
        clock=clock,
    )

    assert tried == [0.0, 2.0, 4.0, 6.0]


def test_it_imports_without_loading_glfw_or_openxr():
    code = (
        "import fun_time_vr.bringup, sys; "
        "loaded = [m for m in sys.modules if m in ('glfw', 'xr')]; "
        "assert not loaded, loaded"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0, result.stderr
