"""The windows a session's children would own, stood in for off Windows.

Both the :class:`fun_time.role_windows.WindowRoles` tests and the dispatch
loop's drive the same imaginary desktop: five children with known pids, the
hosted Origenerator's three same-pid captions, and the browser window startup
captured.  One home for it, so a test in either file that says ``PORTRAIT_HWND``
means the same window a test in the other one does.
"""
from __future__ import annotations

# HWNDs the role lookups resolve to: portrait, landscape and dashboard by pid;
# Nau by pid (with an exact-title fallback); Genau by title; RFB from the hwnd
# captured at startup.
NAU_HWND = 2001
PORTRAIT_HWND = 3001
LANDSCAPE_HWND = 4001
DASHBOARD_HWND = 5001
GENAU_HWND = 6001
RFB_HWND = 7777

# The hosted Origenerator's three windows, resolved by pid AND caption together.
HOSTED_PID = 900
HOSTED_HWND = 8001
HOSTED_PORTRAIT_HWND = 8002
HOSTED_LANDSCAPE_HWND = 8003

NAU_PID = 200
PORTRAIT_PID = 300
LANDSCAPE_PID = 400
DASHBOARD_PID = 500

PID_TO_HWND = {
    NAU_PID: NAU_HWND,
    PORTRAIT_PID: PORTRAIT_HWND,
    LANDSCAPE_PID: LANDSCAPE_HWND,
    DASHBOARD_PID: DASHBOARD_HWND,
}

# The windows that are topmost in EVERY mode — the ones that own a rect and so
# overlap nothing.  Nau and Genau SHARE the main player's rect, so each is in
# the band only in the modes where it shows something; every test folds those
# two in or out as its own mode requires.
TOPMOST_HWNDS = {RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND}


def lookup_pid(pid):
    return PID_TO_HWND.get(pid, 0)


def lookup_title(title, exact=False):
    return GENAU_HWND if title == "Genau" and not exact else 0


def lookup_hosted(pid, title):
    """The hosted app's windows, which resolve by pid AND caption together."""
    if pid != HOSTED_PID:
        return 0
    return {
        "Origenerator": HOSTED_HWND,
        "Origenerator Portrait": HOSTED_PORTRAIT_HWND,
        "Origenerator Landscape": HOSTED_LANDSCAPE_HWND,
    }.get(title, 0)


class FakeClock:
    """A monotonic clock a test drives by hand, so the settle a mode switch
    waits out can genuinely run out instead of being back-dated by reaching
    into the deadline map."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
