from __future__ import annotations

import logging
import os
from pathlib import Path

import pygame
from app_support.cli import preparse_config_path
from app_support.osr2_slot import Osr2Sender
from app_support.win32 import set_app_user_model_id
from funestra.funestra import Funestra
from funestra.funestra_priority import ahead_on_the_processor
from funestra.funestra_setup import FunestraSetup
from funestra.genau_notifier import GenauNotifier
from funestra.messages import MessageWriter
from funestra.modes import MainMode

from fun_time.genau_config import GenauSettings

from .cli import (
    audio_muted,
    build_parser,
    kino_on,
    library_source,
    load_config,
    mode_memory,
    resolve_playlist,
)
from .contract import MainChannels
from .genau import ROBOT_HAND, Genau, GenauChannels, GenauFlicks, GenauOutputs
from .genau_verbs import orientations_from
from .input import Input
from .loading import LoadingCanceled, LoadingScreen
from .window import (
    before_the_window_exists,
    replace_pygames_logo_with,
    run_the_window,
    take_outside_resizes,
)

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser(_defaults_from_the_config_the_line_names(argv)).parse_args(argv)
    with ahead_on_the_processor():
        return _run(args)


def _defaults_from_the_config_the_line_names(argv: list[str] | None) -> dict:
    named = preparse_config_path(argv)
    return load_config(Path(named)) if named else {}


def _genau(args):
    if args.genau_command_file is None:
        return None
    channels = GenauChannels(
        command=args.genau_command_file, paused=args.genau_paused_file,
        status=args.genau_status_file, drive=args.drive_file)
    return lambda _playback: Genau(
        GenauFlicks(
            folder=args.flicks_dir,
            start_flick=Path(args.genau_start_flick) if args.genau_start_flick else None,
            latest=args.genau_latest,
            f_mode=args.genau_f_mode,
            orientations=orientations_from(args.genau_orientations),
            metadata_root=Path(args.metadata_dir) if args.metadata_dir else None,
        ),
        settings=GenauSettings.read(args.config),
        channels=channels,
        outputs=GenauOutputs(
            notifier=GenauNotifier(args.notify_host, args.notify_port),
            tcode_sink=Osr2Sender(ROBOT_HAND, port=args.osr2_port),
            messages=MessageWriter(args.notice_file),
            origenerator_commands=args.origenerator_command_file,
        ),
    )


def _users(args, *, kino):
    users = {MainMode.KINO: kino}
    genau = _genau(args)
    if genau is not None:
        users[MainMode.GENAU] = genau
    return users


def _take_fun_times_taskbar_identity(taskbar_identity: str | None) -> None:
    if not taskbar_identity:
        return
    try:
        set_app_user_model_id(taskbar_identity)
    except OSError:
        logger.debug("No taskbar identity claimed", exc_info=True)


def _open_window(args):
    before_the_window_exists()
    pygame.init()
    if args.x is not None and args.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{args.x},{args.y}"
    replace_pygames_logo_with(pygame, args.icon)
    screen = pygame.display.set_mode((args.width, args.height), pygame.NOFRAME)
    take_outside_resizes(pygame)
    pygame.display.set_caption("Main Funestra")
    return screen


def _run(args) -> int:
    _take_fun_times_taskbar_identity(args.taskbar_identity)
    screen = _open_window(args)
    wid = pygame.display.get_wm_info()["window"]
    memory = mode_memory(args)
    loading = LoadingScreen(screen, icon=args.icon)
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

    funestra = Funestra.on_window(wid, FunestraSetup(
        channels=MainChannels.from_args(args), playlist=items,
        audible=not audio_muted(args), tiles=True, locked=True, sound_is_the_rooms=True,
        users=_users(args, kino=kino_on(args, source=source, memory=memory)),
    ))
    run_the_window(pygame, funestra, Input(funestra).deal)
    return 0
