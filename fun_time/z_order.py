"""Centralized z-order management for all fun_time windows.

Replaces the scattered set_always_on_top calls across sequencer,
orchestrator, and dispatch loop with a single source of truth.
"""
from __future__ import annotations

from .win32 import is_window_topmost, set_always_on_top


def compute_z_order(
    *,
    rfb_hwnd: int = 0,
    portrait_hwnd: int = 0,
    landscape_hwnd: int = 0,
    primary_hwnd: int = 0,
    genau_hwnd: int = 0,
    mfp_hwnd: int = 0,
    dashboard_hwnd: int = 0,
    primary_mode: str = "vlc",
) -> list[tuple[int, bool]]:
    """Compute the desired z-order stack (bottom to top).

    Returns a list of (hwnd, should_be_topmost) tuples.  Within the
    TOPMOST band the last window to receive SetWindowPos(HWND_TOPMOST)
    goes on top, so the list order determines visual stacking.

    Stack (bottom to top)::

        RFB > Portrait > Landscape > [Primary|Genau] > MFP > Dashboard

    In VLC mode, Genau is non-topmost.  In Genau mode, Primary is
    non-topmost.  In Hybrid mode, both are topmost with Genau on top.
    """
    layers: list[tuple[int, bool]] = []

    if rfb_hwnd:
        layers.append((rfb_hwnd, True))
    if portrait_hwnd:
        layers.append((portrait_hwnd, True))
    if landscape_hwnd:
        layers.append((landscape_hwnd, True))

    if primary_mode == "genau":
        if primary_hwnd:
            layers.append((primary_hwnd, False))
        if genau_hwnd:
            layers.append((genau_hwnd, True))
    elif primary_mode == "hybrid":
        if primary_hwnd:
            layers.append((primary_hwnd, True))
        if genau_hwnd:
            layers.append((genau_hwnd, True))
    else:
        if genau_hwnd:
            layers.append((genau_hwnd, False))
        if primary_hwnd:
            layers.append((primary_hwnd, True))

    if mfp_hwnd:
        layers.append((mfp_hwnd, True))
    if dashboard_hwnd:
        layers.append((dashboard_hwnd, True))

    return layers


def apply_z_order(layers: list[tuple[int, bool]], *, reorder: bool = True) -> None:
    """Apply the z-order stack via SetWindowPos.

    When *reorder* is True (default), demotes ALL windows first so the
    subsequent promote-from-bottom-to-top establishes the correct
    stacking.  Use this at startup and after transitions (genau toggle,
    omnipause leave) where the full ordering must be rebuilt.

    When *reorder* is False, only demotes windows that should NOT be
    topmost.  Already-topmost windows are left untouched, avoiding the
    visual flicker that a full demote-all causes.  Use this for periodic
    drift correction (sync tick) where only the Primary/Genau pair may
    need fixing.
    """
    if reorder:
        for hwnd, _ in layers:
            if hwnd:
                set_always_on_top(hwnd, False)
        for hwnd, topmost in layers:
            if hwnd and topmost:
                set_always_on_top(hwnd, True)
    else:
        # Drift correction: only call SetWindowPos when the current
        # state differs from desired.  In the steady state this makes
        # zero SetWindowPos calls, eliminating visual flicker.
        for hwnd, topmost in layers:
            if hwnd and is_window_topmost(hwnd) != topmost:
                set_always_on_top(hwnd, topmost)
