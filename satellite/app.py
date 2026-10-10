"""Fun Time's satellite program: a Funestra on a borderless window the session places.

A shell: the Funestra it runs on that window is player_core's, and what is
tested here is the window and the loop (tests/test_satellite_app_loop.py).
"""
from __future__ import annotations

import logging
import os

import pygame
from app_support.logging_utils import install_exception_logging
from app_support.win32 import set_app_user_model_id
from player_core.funestra import Funestra
from player_core.playlist import PlaylistItem
from player_core.sdl_hints import deliver_the_focusing_click

from main_player.input import Input
from main_player.player_window import take_outside_resizes, wear_the_icon

from .cli import audio_muted, build_parser, resolve_playlist
from .contract import SatelliteChannels, WindowPlacement

logger = logging.getLogger(__name__)


def set_up_logging() -> logging.Logger:
    """This process's own log, and its own crash in it."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    install_exception_logging(logger)
    return logger


def main(argv: list[str] | None = None) -> int:
    set_up_logging()
    args = build_parser().parse_args(argv)
    playlist = resolve_playlist(args)
    if not playlist:
        logger.error("No videos to play (need --playlist)")
        return 1
    logger.info("Satellite playing %d clip(s)", len(playlist))
    return _run(args, playlist)


def _open_window(placement: WindowPlacement) -> int:
    """Put this satellite's borderless window on screen; return its HWND.  The
    order here is the whole content of the function, and each step says why."""
    # Before the window exists, and before pygame.init(): SDL otherwise eats the
    # click that focuses this window, so every press on a control had to be made
    # twice.  The mechanism is written down in player_core.sdl_hints.
    deliver_the_focusing_click()
    # Also before the window: Windows reads a process's taskbar identity as each
    # window is created, and a satellite that claims none is filed under whatever
    # the shared interpreter's path is registered to — some unrelated program,
    # wearing its icon.  Cosmetic, so a refusal never costs the player its start.
    if placement.taskbar_identity:
        try:
            set_app_user_model_id(placement.taskbar_identity)
        except OSError:
            logger.info("Could not take the taskbar identity %s",
                        placement.taskbar_identity)
    pygame.init()
    if placement.x is not None and placement.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{placement.x},{placement.y}"
    wear_the_icon(pygame, placement.icon)
    # Borderless, so the client area IS the slot: mpv paints into this window via
    # its HWND (the pygame surface is never blitted) and the sequencer sizes it to
    # the portrait/landscape rect.
    pygame.display.set_mode((placement.width, placement.height), pygame.NOFRAME)
    take_outside_resizes(pygame)
    # A distinct --title per satellite, so the sequencer can resolve each window
    # to its slot by title when the pid lookup fails; also its Alt-Tab name.
    pygame.display.set_caption(placement.title)
    return pygame.display.get_wm_info()["window"]


def _run(args, playlist: list[PlaylistItem]) -> int:
    placement = WindowPlacement.from_args(args)
    funestra = Funestra.on_window(
        _open_window(placement), channels=SatelliteChannels.from_args(args), playlist=playlist,
        audible=not audio_muted(args), tiles=placement.tiles,
    )
    clock = pygame.time.Clock()
    window_input = Input(funestra)
    while not funestra.stopped:
        # Before the events, which have to be placed against the window they
        # landed in; the sequencer can move this one between passes.
        window = pygame.display.get_window_size()
        window_input.deal(pygame.event.get(), window)
        funestra.tick(window=window)
        clock.tick(60)
    funestra.close()
    pygame.quit()
    return 0
