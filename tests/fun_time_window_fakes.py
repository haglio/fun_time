"""The windows a session's children would own, stood in for off Windows.

Both the :class:`fun_time.fun_time_windows.FunTimeWindows` tests and the dispatch
loop's drive the same imaginary desktop: four children with known pids, the
hosted Origenerator's own caption, and the browser window startup
captured.  One home for it, so a test in either file that says ``PORTRAIT_HWND``
means the same window a test in the other one does.
"""
from __future__ import annotations

MAIN_FUNESTRA_HWND = 2001
PORTRAIT_HWND = 3001
LANDSCAPE_HWND = 4001
DASHBOARD_HWND = 5001
RFB_HWND = 7777

HOSTED_PID = 900
HOSTED_HWND = 8001

MAIN_FUNESTRA_PID = 200
PORTRAIT_PID = 300
LANDSCAPE_PID = 400
DASHBOARD_PID = 500

PID_TO_HWND = {
    MAIN_FUNESTRA_PID: MAIN_FUNESTRA_HWND,
    PORTRAIT_PID: PORTRAIT_HWND,
    LANDSCAPE_PID: LANDSCAPE_HWND,
    DASHBOARD_PID: DASHBOARD_HWND,
}

TOPMOST_HWNDS = {RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND, MAIN_FUNESTRA_HWND}


def lookup_pid(pid):
    return PID_TO_HWND.get(pid, 0)


def lookup_title(title, exact=False):
    return 0


def lookup_hosted(pid, title, *, include_hidden=False, caption="Origenerator"):
    if pid != HOSTED_PID or not include_hidden:
        return 0
    accepted = title(caption) if callable(title) else title == caption
    return HOSTED_HWND if accepted else 0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
