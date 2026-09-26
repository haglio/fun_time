from __future__ import annotations

import pygame
import pytest

from main_player.library_source import PHASE_DISCOVER, PHASE_DURATIONS
from main_player.loading import (
    REPAINT_INTERVAL_S,
    LoadingCanceled,
    progress_fraction,
    progress_text,
    quit_requested,
    repaint_due,
    stop_if_asked,
)


def _event(kind, **fields):
    return pygame.event.Event(kind, **fields)


class TestQuitRequested:
    """A wait long enough to need a loading screen is long enough that closing
    the window has to work during it."""

    def test_closing_the_window_quits(self):
        assert quit_requested([pygame.event.Event(pygame.QUIT)])

    def test_no_key_does_not_even_ctrl_q(self):
        """The room's keyboard is Fun Time's, whose quit is Ctrl+Alt+Q, and
        playback answers no key of its own either."""
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q, mod=pygame.KMOD_CTRL)
        assert not quit_requested([event])


class TestRepaintDue:
    """A warm cache reports all 525 entries in a fraction of a second; painting
    every one of them would cost more than the work being reported."""

    def test_holds_off_within_the_interval(self):
        assert not repaint_due(
            phase="durations", last_phase="durations", now=10.0, last_paint_s=10.0,
        )

    def test_paints_once_the_interval_has_passed(self):
        assert repaint_due(
            phase="durations", last_phase="durations",
            now=10.0 + REPAINT_INTERVAL_S, last_paint_s=10.0,
        )

    def test_a_new_phase_always_paints(self):
        assert repaint_due(
            phase="durations", last_phase="discover", now=10.0, last_paint_s=10.0,
        )


class TestProgressText:
    def test_counting_phase_shows_how_far(self):
        assert progress_text(PHASE_DURATIONS, 128, 525) == "Reading video lengths... 128 of 525"

    def test_uncounted_phase_is_just_the_message(self):
        assert progress_text(PHASE_DISCOVER, 0, 0) == "Finding videos..."


class TestProgressFraction:
    def test_no_total_is_indeterminate(self):
        assert progress_fraction(0, 0) is None

    def test_counts_the_work_behind_it(self):
        assert progress_fraction(1, 4) == 0.25


class TestGivingUpOnTheWait:
    """The whole of `LoadingScreen.update` that is not painting.

    A cold library scan runs to tens of seconds, and the close button during it
    is the only way out: `main_player.app` catches `LoadingCanceled` and exits 0 without
    ever opening a video.  The pump is owed on every update either way, or
    Windows grays the window out as unresponsive halfway through.
    """

    def test_the_close_button_ends_the_wait(self, monkeypatch):
        monkeypatch.setattr(pygame.event, "get", lambda: [_event(pygame.QUIT)])

        with pytest.raises(LoadingCanceled):
            stop_if_asked()

    def test_an_ordinary_key_does_not(self, monkeypatch):
        monkeypatch.setattr(
            pygame.event, "get",
            lambda: [_event(pygame.KEYDOWN, key=pygame.K_j, mod=0)])

        stop_if_asked()  # the control probe: it must not raise on anything else

    def test_it_pumps_the_queue_even_with_nothing_in_it(self, monkeypatch):
        """The pump is the half that has nothing to do with quitting, and the
        half Windows notices when it stops happening."""
        pumped = []
        monkeypatch.setattr(
            pygame.event, "get", lambda: pumped.append(1) or [])

        stop_if_asked()

        assert pumped == [1]
