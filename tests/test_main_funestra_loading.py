from __future__ import annotations

import pygame
import pytest
from shared_ui.loading_panel import LoadingPanel
from shared_ui.palette import LOADING_ACCENT, LOADING_GROUND, LOADING_TROUGH

from main_funestra.library_source import PHASE_DISCOVER
from main_funestra.loading import (
    LoadingCanceled,
    paint_panel,
    progress_text,
    quit_requested,
    stop_if_asked,
)


def _event(kind, **fields):
    return pygame.event.Event(kind, **fields)


class TestQuitRequested:
    def test_closing_the_window_quits(self):
        assert quit_requested([pygame.event.Event(pygame.QUIT)])

    def test_no_key_does_not_even_ctrl_q(self):
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q, mod=pygame.KMOD_CTRL)
        assert not quit_requested([event])


class TestProgressText:
    def test_the_walk_is_said_in_words(self):
        assert progress_text(PHASE_DISCOVER) == "Finding videos..."

    def test_the_build_names_its_phase_and_only_the_screen_puts_words_to_it(self):
        assert PHASE_DISCOVER.isidentifier()
        assert progress_text(PHASE_DISCOVER) != PHASE_DISCOVER


class TestGivingUpOnTheWait:
    def test_the_close_button_ends_the_wait(self, monkeypatch):
        monkeypatch.setattr(pygame.event, "get", lambda: [_event(pygame.QUIT)])

        with pytest.raises(LoadingCanceled):
            stop_if_asked()

    def test_an_ordinary_key_does_not(self, monkeypatch):
        monkeypatch.setattr(
            pygame.event, "get",
            lambda: [_event(pygame.KEYDOWN, key=pygame.K_j, mod=0)])

        stop_if_asked()

    def test_it_pumps_the_queue_even_with_nothing_in_it(self, monkeypatch):
        pumped = []
        monkeypatch.setattr(
            pygame.event, "get", lambda: pumped.append(1) or [])

        stop_if_asked()

        assert pumped == [1]


class TestWhatTheFunestraPaintsWhileItReads:
    def test_it_is_the_familys_loading_panel_in_the_middle_of_the_window(self):
        surface = pygame.Surface((800, 600))

        paint_panel(surface, LoadingPanel(wordmark="Main Funestra", status="Finding videos...", fraction=0.5))

        assert surface.get_at((0, 0))[:3] == LOADING_GROUND
        tones = {tuple(surface.get_at((x, y))[:3]) for y in range(0, 600, 2) for x in range(0, 800, 2)}
        assert LOADING_ACCENT in tones
        assert LOADING_TROUGH in tones
