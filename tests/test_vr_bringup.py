"""Opening a session on the headset, and what is said when it cannot be opened."""
from __future__ import annotations

from fun_time_vr.bringup import open_vr_session


class _DeviceNotReady(Exception):
    """Stands in for the runtime's own "graphics device invalid"."""


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
        now=clock.now,
        sleep=clock.sleep,
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
    """The runtime answers the readiness probe before its compositor's graphics
    device is up, and opening a session in that window fails — transiently, so
    bring-up retries rather than dying on a popup."""
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
    """Only the cold-device case is transient; everything else is a session this
    headset is not going to give, so retrying it just delays the popup."""
    clock = _Clock()

    opened = _open([_raises(RuntimeError("no runtime"))], clock)

    assert opened.session is None
    assert "could not open a session" in opened.message
    assert "no runtime" in opened.message
    assert clock.slept == []


def test_the_deadline_is_measured_from_the_first_attempt():
    """A retry loop that re-read the clock as a fresh start would never end: the
    window is six seconds and each retry costs two, so four tries fit."""
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
        now=clock.now,
        sleep=clock.sleep,
    )

    assert tried == [0.0, 2.0, 4.0, 6.0]
