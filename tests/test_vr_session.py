"""The VR session's decisions that do not need a headset to check.

Everything else in :mod:`fun_time_vr.vr_session` wants the OpenXR loader, a
runtime and a live GL context; whether a located view is worth rendering from
is pure, and so is what the runtime's own session states ask of the session --
both decide whether anything reaches the headset at all.
"""
from __future__ import annotations

import xr

from fun_time_vr.vr_session import VRSession, views_are_renderable, views_are_tracked


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


def test_renderable_is_not_the_same_question_as_tracked():
    """The distinction the loading panel turned on: a VALID orientation is a
    last-known or predicted pose, good enough to draw from and useless for
    deciding where a thing has to be so that he sees it.  Placed off a merely
    valid pose, the panel sat at the reference space's forward while he was
    facing somewhere else, and he reported no loading screen at all."""
    valid_only = xr.ViewStateFlags.ORIENTATION_VALID_BIT

    assert views_are_renderable(valid_only) is True
    assert views_are_tracked(valid_only) is False
    assert views_are_tracked(valid_only | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT) is True


def test_an_untracked_view_is_still_worth_drawing():
    """Waiting for TRACKED to draw would put the runtime's own environment back
    over a session that has a perfectly good pose to render from."""
    assert views_are_renderable(xr.ViewStateFlags.ORIENTATION_VALID_BIT) is True


class _SessionStateEvents:
    """``xr.poll_event``, feeding a queue of state changes and then running dry."""

    def __init__(self, *states):
        self._queue = list(states)

    def __call__(self, _instance):
        if not self._queue:
            raise xr.EventUnavailable
        event = xr.EventDataSessionStateChanged(state=self._queue.pop(0))
        event.type = xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED
        return event


def _a_session_off_a_headset(monkeypatch, *states):
    """A session with no bring-up: the few fields its event poll reads, and the
    three runtime calls it makes recorded rather than sent."""
    session = VRSession.__new__(VRSession)
    session.running = True
    session._instance = object()
    session._session = object()
    session._session_state = xr.SessionState.UNKNOWN
    session._session_begun = False
    calls: list[str] = []
    monkeypatch.setattr(xr, "poll_event", _SessionStateEvents(*states))
    monkeypatch.setattr(xr, "begin_session", lambda *_a, **_kw: calls.append("begin"))
    monkeypatch.setattr(xr, "end_session", lambda *_a, **_kw: calls.append("end"))
    return session, calls


def test_a_session_the_runtime_has_readied_is_ready_to_submit_frames(monkeypatch):
    session, calls = _a_session_off_a_headset(monkeypatch, xr.SessionState.READY)

    session.poll_events()

    assert calls == ["begin"]
    assert session.session_ready is True


def test_a_stopped_session_is_no_longer_ready_to_submit_frames(monkeypatch):
    """STOPPING ends the session, and a session that has ended takes no frames.

    The flag was set on READY and never cleared, so the player's frame loop
    went on submitting into an ended session and the app could die there
    (bug 18).
    """
    session, calls = _a_session_off_a_headset(
        monkeypatch, xr.SessionState.READY, xr.SessionState.STOPPING)

    session.poll_events()

    assert calls == ["begin", "end"]
    assert session.session_ready is False


def test_a_session_that_comes_back_is_ready_again(monkeypatch):
    """The runtime readies a stopped session again when the headset is worn
    once more, so the way back has to work as well as the way out."""
    session, _calls = _a_session_off_a_headset(
        monkeypatch, xr.SessionState.READY, xr.SessionState.STOPPING,
        xr.SessionState.READY)

    session.poll_events()

    assert session.session_ready is True
