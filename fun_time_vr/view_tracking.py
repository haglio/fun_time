from __future__ import annotations

import xr


def views_are_renderable(view_state_flags: int) -> bool:
    return bool(view_state_flags & xr.ViewStateFlags.ORIENTATION_VALID_BIT)


def views_are_tracked(view_state_flags: int) -> bool:
    return bool(view_state_flags & xr.ViewStateFlags.ORIENTATION_TRACKED_BIT)
