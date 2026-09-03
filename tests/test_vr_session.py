"""The VR session's one decision that does not need a headset to check.

Everything else in :mod:`fun_time_vr.vr_session` wants the OpenXR loader, a
runtime and a live GL context; whether a located view is worth rendering from
is pure, and it decides whether anything reaches the headset at all.
"""
from __future__ import annotations

import xr

from fun_time_vr.vr_session import views_are_renderable


def test_a_fully_tracked_view_is_renderable():
    flags = (
        xr.ViewStateFlags.ORIENTATION_VALID_BIT
        | xr.ViewStateFlags.POSITION_VALID_BIT
        | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT
        | xr.ViewStateFlags.POSITION_TRACKED_BIT
    )
    assert views_are_renderable(flags) is True


def test_orientation_alone_is_enough():
    """Inside-out tracking drops to orientation-only in a dim room, and used to
    take the whole picture with it: nothing was submitted and the headset showed
    the runtime's own pass-through.  The renderer never reads head position --
    the eye pass passes (0, 0, 0) on purpose -- so there is nothing to wait for.
    """
    assert views_are_renderable(xr.ViewStateFlags.ORIENTATION_VALID_BIT) is True


def test_an_unlocated_view_is_not_renderable():
    """The check exists for these: an unlocated view reports an all-zero FOV,
    which is a zero-width frustum and a division by zero in the projection."""
    assert views_are_renderable(0) is False
    assert views_are_renderable(xr.ViewStateFlags.POSITION_VALID_BIT) is False
