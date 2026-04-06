"""Centralized z-order management for all fun_time windows.

Replaces the scattered set_always_on_top calls across sequencer,
orchestrator, and dispatch loop with a single source of truth.
"""
from __future__ import annotations

from .win32 import set_always_on_top


def compute_z_order(
    *,
    rfb_hwnd: int = 0,
    portrait_hwnd: int = 0,
    landscape_hwnd: int = 0,
    primary_hwnd: int = 0,
    genau_hwnd: int = 0,
    mfp_hwnd: int = 0,
    dashboard_hwnd: int = 0,
    genau_active: bool = False,
) -> list[tuple[int, bool]]:
    """Compute the desired z-order stack (bottom to top).

    Returns a list of (hwnd, should_be_topmost) tuples.  Within the
    TOPMOST band the last window to receive SetWindowPos(HWND_TOPMOST)
    goes on top, so the list order determines visual stacking.

    Stack (bottom to top)::

        RFB > Portrait > Landscape > [Primary|Genau] > MFP > Dashboard

    The inactive window (Genau when not active, Primary when Genau is
    active) gets ``topmost=False``.
    """
    layers: list[tuple[int, bool]] = []

    if rfb_hwnd:
        layers.append((rfb_hwnd, True))
    if portrait_hwnd:
        layers.append((portrait_hwnd, True))
    if landscape_hwnd:
        layers.append((landscape_hwnd, True))

    if genau_active:
        if primary_hwnd:
            layers.append((primary_hwnd, False))
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


def apply_z_order(layers: list[tuple[int, bool]]) -> None:
    """Apply the z-order stack via SetWindowPos.

    First demotes ALL windows to the regular z-band, then sets TOPMOST
    from bottom to top.  The demote-then-promote pattern ensures correct
    ordering even when a window has re-asserted TOPMOST externally
    (e.g. Dashboard's Qt WindowStaysOnTopHint, VLC video transitions).
    """
    for hwnd, _ in layers:
        if hwnd:
            set_always_on_top(hwnd, False)

    for hwnd, topmost in layers:
        if hwnd and topmost:
            set_always_on_top(hwnd, True)
