"""Fun Time's Main Player: the Main Funestra, with Kino running on it.

A shell: it opens the window, reads the library while the loading screen says
so, and hands the window to a Funestra that plays the list and draws the
console, with Kino in the same process answering what only the library side
knows.  What is tested here is the window and the wiring
(tests/test_main_player_app.py); the Funestra and Kino are tested on their own.
"""
from __future__ import annotations

import logging
import os
from functools import partial

import pygame
from app_support.win32 import set_app_user_model_id
from player_core.funestra import Funestra
from player_core.sdl_hints import deliver_the_focusing_click

from .cli import (
    audio_muted,
    build_parser,
    library_source,
    load_config,
    mode_memory,
    resolve_playlist,
)
from .clip_nav import ClipNav
from .contract import MainChannels
from .input import Input
from .kino import Kino
from .loading import LoadingCanceled, LoadingScreen
from .notice import NoticeWriter
from .player_window import take_outside_resizes, wear_the_icon

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser({}).parse_args(argv)
    if args.config is not None:
        args = build_parser(load_config(args.config)).parse_args(argv)
    return _run(args)


def _set_aumid(taskbar_identity: str | None) -> None:
    if not taskbar_identity:
        return
    try:
        set_app_user_model_id(taskbar_identity)
    except OSError:
        logger.debug("No taskbar identity claimed", exc_info=True)


def _open_window(args):
    deliver_the_focusing_click()
    pygame.init()
    if args.x is not None and args.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{args.x},{args.y}"
    wear_the_icon(pygame, args.icon)
    screen = pygame.display.set_mode((args.width, args.height), pygame.NOFRAME)
    take_outside_resizes(pygame)
    pygame.display.set_caption("Main Player")
    return screen


def _run(args) -> int:
    _set_aumid(args.taskbar_identity)
    screen = _open_window(args)
    wid = pygame.display.get_wm_info()["window"]
    memory = mode_memory(args)
    remembered = memory.read()
    loading = LoadingScreen(screen)
    try:
        source = library_source(args, on_progress=loading.update)
        items = resolve_playlist(args, source=source)
    except LoadingCanceled:
        logger.info("Closed while loading; never started playback")
        pygame.quit()
        return 0
    if not items:
        logger.error("Nothing to play in the playlist Fun Time passed")
        pygame.quit()
        return 1
    scripted = sum(1 for item in items if item.funscript is not None)
    logger.info("Found %d item(s), %d with funscripts", len(items), scripted)

    entries = source.entries if source is not None else []
    clip_nav = ClipNav.build(
        [e.video for e in entries] + [c.video for c in (source.genau_clips if source else [])],
        source.metadata_root if source is not None else None,
    )
    funestra = Funestra.on_window(
        wid, channels=MainChannels.from_args(args), playlist=items,
        audible=not audio_muted(args), tiles=True, locked=True, sound_is_the_rooms=True,
        user=partial(
            Kino, source=source, clip_nav=clip_nav, notices=NoticeWriter(args.notice_file),
            memory=memory, remembered=remembered,
            resolve_playlist=partial(resolve_playlist, args, source=source)),
    )
    window_input = Input(funestra)
    clock = pygame.time.Clock()
    while not funestra.stopped:
        window = pygame.display.get_window_size()
        window_input.deal(pygame.event.get(), window)
        funestra.tick(window=window)
        clock.tick(60)
    funestra.close()
    pygame.quit()
    return 0
