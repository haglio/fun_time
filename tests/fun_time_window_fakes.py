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

_EVERY_MANAGED_HWND = {RFB_HWND, PORTRAIT_HWND, LANDSCAPE_HWND, DASHBOARD_HWND,
                       MAIN_FUNESTRA_HWND, HOSTED_HWND}
_HWNDS_SHARING_THE_BROWSERS_RECT = {HOSTED_HWND}
TOPMOST_HWNDS = _EVERY_MANAGED_HWND - _HWNDS_SHARING_THE_BROWSERS_RECT


def lookup_pid(pid):
    return PID_TO_HWND.get(pid, 0)


def lookup_title(title, exact=False):
    return 0


def lookup_hosted(pid, title, *, include_hidden=False, caption="Origenerator"):
    overlooks_a_window_parked_since_it_booted = not include_hidden
    if pid != HOSTED_PID or overlooks_a_window_parked_since_it_booted:
        return 0
    accepted = title(caption) if callable(title) else title == caption
    return HOSTED_HWND if accepted else 0


class HandDrivenClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
