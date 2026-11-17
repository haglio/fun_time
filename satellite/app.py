from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pygame
from app_support.logging_utils import install_exception_logging
from app_support.win32 import set_app_user_model_id
from funestra.channel_manifest import channel_flag
from funestra.funestra import Funestra
from funestra.funestra_priority import ahead_on_the_processor
from funestra.funestra_setup import FunestraSetup
from funestra.funestra_verbs import QUIT
from funestra.playlist import PlaylistItem, read_playlist

from main_funestra.input import Input
from main_funestra.window import (
    before_the_window_exists,
    claim_the_sessions_taskbar_identity,
    replace_pygames_logo_with,
    run_the_window,
    take_outside_resizes,
    wear_the_sessions_taskbar_button,
)

from .cli import audio_muted, build_parser, resolve_playlist
from .contract import SatelliteChannels, WindowPlacement

logger = logging.getLogger(__name__)

_LOOK_FOR_A_LIST_EVERY_S = 0.1


def set_up_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    install_exception_logging(logger)
    return logger


def main(argv: list[str] | None = None) -> int:
    set_up_logging()
    args = build_parser().parse_args(argv)
    if args.playlist is None:
        logger.error("No videos to play (need %s)", channel_flag("playlist"))
        return 1
    with ahead_on_the_processor():
        return _run(args, resolve_playlist(args))


def _open_window(placement: WindowPlacement, claim_identity) -> int:
    # Before the window exists, and before pygame.init(): SDL otherwise eats the
    # click that focuses this window, so every press on a control had to be made
    # twice.  The mechanism is written down in funestra.sdl_hints.
    before_the_window_exists()
    # Also before the window: Windows reads a process's taskbar identity as each
    # window is created, and a satellite that claims none is filed under whatever
    # the shared interpreter's path is registered to — some unrelated program,
    # wearing its icon.
    claim_the_sessions_taskbar_identity(placement.taskbar_identity, claim=claim_identity)
    pygame.init()
    if placement.x is not None and placement.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{placement.x},{placement.y}"
    replace_pygames_logo_with(pygame, placement.icon)
    # Borderless, so the client area IS the place it was given: mpv paints into this window via
    # its HWND (the pygame surface is never blitted) and the sequencer sizes it to
    # the portrait/landscape rect.
    pygame.display.set_mode((placement.width, placement.height), pygame.NOFRAME)
    take_outside_resizes(pygame)
    pygame.display.set_caption(placement.title)
    hwnd = pygame.display.get_wm_info()["window"]
    wear_the_sessions_taskbar_button(hwnd, placement.taskbar_identity)
    return hwnd


def _the_list_once_it_is_written(channels: SatelliteChannels) -> list[PlaylistItem]:
    while True:
        pygame.event.get()
        if QUIT in _lines_of(channels.command):
            return []
        playlist = read_playlist(channels.playlist)
        if playlist:
            return playlist
        time.sleep(_LOOK_FOR_A_LIST_EVERY_S)


def _lines_of(path: Path | None) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").split() if path else []
    except OSError:
        return []


def _run(args, playlist: list[PlaylistItem], *, claim_identity=set_app_user_model_id) -> int:
    placement = WindowPlacement.from_args(args)
    channels = SatelliteChannels.from_args(args)
    window = _open_window(placement, claim_identity)
    playlist = playlist or _the_list_once_it_is_written(channels)
    if not playlist:
        pygame.quit()
        return 0
    logger.info("Satellite playing %d clip(s)", len(playlist))
    funestra = Funestra.on_window(window, FunestraSetup(
        channels=channels, playlist=playlist,
        audible=not audio_muted(args), tiles=placement.tiles,
    ))
    run_the_window(pygame, funestra, Input(funestra).deal)
    return 0
