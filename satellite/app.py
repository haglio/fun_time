"""Run loop for a native satellite player: an mpv window fun_time drives.

The satellite half of Nau's app shell, stripped to essentials — no funscript,
tcode, heatmap, record or version cycling.  mpv renders the video into a
pygame/SDL window; fun_time positions that window by HWND after launch and drives
playback through the command + paused files, reading back the status file.  Three
things are composited on top: the lock HUD from the panel fun_time publishes, the
scrubber and the volume chip — the last two taking this loop's own mouse events.

A shell: the control logic it drives lives in satellite.session,
satellite.runtime, satellite.pointer, satellite.volume and
player_core.satellite_hud*.  See CLAUDE.md, "Standing rules", for why nothing
here is unit-tested.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np
import pygame
from app_support.win32 import set_app_user_model_id
from player_core.file_channel import consume_command_file, read_paused_state
from player_core.mpv_player import MpvPlayer
from player_core.sdl_hints import deliver_the_focusing_click
from player_core.session_quit import quit_gesture
from player_core.status import StatusWriter
from player_core.timeline import TIMELINE_HEIGHT, progress_bar_bgra
from player_core.volume import VolumeHudPainter, chip_xy

from .cli import audio_muted, build_parser, resolve_playlist
from .hud_overlay import HudOverlay
from .pointer import Pointer
from .runtime import apply_command
from .session import SatelliteSession
from .status import status_fields
from .volume import SatelliteVolume

logger = logging.getLogger(__name__)

# Overlay ids, against the lock HUD's 10.  mpv draws them in ascending order, so
# the blackout sits UNDER the HUD (whose mode row is the way back off a blacked
# player) and the two above it are simply not drawn while black.
_OV_BLACKOUT = 5
_OV_SCRUBBER = 11
_OV_VOLUME = 12

# Fun Time's own icon, so a satellite's Alt-Tab entry and taskbar button say
# which application it belongs to.  Without one, pygame supplies its own logo and
# these windows read as some unrelated program.  Kept here rather than taken from
# `fun_time.project_paths`: this package imports nothing from fun_time at all.
ICON_PATH = Path(__file__).resolve().parent.parent / "icon.ico"


def _load_icon_surface():
    """Fun Time's icon as a pygame surface, or None if it cannot be read.

    Must be set before ``set_mode``: SDL takes the icon from the display at
    window creation, so a later call has nothing to apply it to.
    """
    if not ICON_PATH.exists():
        return None
    try:
        from PIL import Image

        image = Image.open(ICON_PATH).convert("RGBA")
        return pygame.image.frombytes(image.tobytes(), image.size, "RGBA")
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    playlist = resolve_playlist(args)
    if not playlist:
        logger.error("No videos to play (need --playlist)")
        return 1
    logger.info("Satellite playing %d clip(s)", len(playlist))
    return _run(args, playlist)


def _run(args, playlist: list[Path]) -> int:
    # Before the window exists, and before pygame.init(): SDL otherwise eats the
    # click that focuses this window, so every press on a control had to be made
    # twice.  The mechanism is written down in player_core.sdl_hints.
    deliver_the_focusing_click()
    # Also before the window: Windows reads a process's taskbar identity as each
    # window is created, and a satellite that claims none is filed under whatever
    # the shared interpreter's path is registered to — some unrelated program,
    # wearing its icon.  Cosmetic, so a refusal never costs the player its start.
    if args.taskbar_identity:
        try:
            set_app_user_model_id(args.taskbar_identity)
        except OSError:
            logger.info("Could not take the taskbar identity %s", args.taskbar_identity)
    pygame.init()
    if args.x is not None and args.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{args.x},{args.y}"
    # Borderless, so the client area IS the slot: mpv paints into this window via
    # its HWND (the pygame surface is never blitted) and the sequencer sizes it to
    # the portrait/landscape rect.
    icon = _load_icon_surface()
    if icon is not None:
        pygame.display.set_icon(icon)  # must precede set_mode to take effect
    pygame.display.set_mode((args.width, args.height), pygame.NOFRAME)
    # A distinct --title per satellite, so the sequencer can resolve each window
    # to its slot by title when the pid lookup fails; also its Alt-Tab name.
    pygame.display.set_caption(args.title)
    clock = pygame.time.Clock()
    wid = pygame.display.get_wm_info()["window"]

    paused_file: Path | None = args.paused_file
    command_file: Path | None = args.command_file
    start_paused = paused_file is not None and read_paused_state(paused_file, logger=logger)

    # loop_file=False so end-of-file advances the playlist; the lock toggles it on.
    # prefetch=True so mpv opens the next clip before the current ends and the
    # auto-advance is seamless instead of a cold on-screen reload.
    # muted=True: a satellite is heard only once its chip is asked (satellite.volume).
    player = MpvPlayer(wid, muted=True, loop_file=False, prefetch=True)
    session = SatelliteSession(playlist, player=player, start_paused=start_paused)
    status_writer = StatusWriter(args.status_file, status_fields) if args.status_file else None
    # Composited into this window's video, so it needs no window of its own.
    hud = (
        HudOverlay(
            hud_file=args.hud_file, command_file=args.dashboard_cmd_file, player=player,
        )
        if args.hud_file and args.dashboard_cmd_file
        else None
    )
    # The scrubber and the volume chip, drawn from the shared engine and taking
    # presses like Nau's: the bar seeks, the chip sets this player's own sound.
    # Missing beside Nau's is only the funscript heatmap, which needs a script a
    # satellite's clips do not carry.
    volume = SatelliteVolume(player, live=not audio_muted(args))
    volume_painter = VolumeHudPainter()
    pointer = Pointer(session=session, volume=volume, hud=hud)
    # The window size the blackout frame was last composited for, or None while
    # the video shows — the frame is re-made only when the size moves.
    blackout_size: tuple[int, int] | None = None
    stop_event = threading.Event()

    def _reload_playlist() -> None:
        reloaded = resolve_playlist(args)
        if reloaded:
            session.replace_playlist(reloaded)

    while not stop_event.is_set():
        # Before the events, which have to be placed against the window they
        # landed in; the sequencer can move this one between passes.
        win_w, win_h = pygame.display.get_window_size()
        for ev in pygame.event.get():
            # No key here ends this player: the session ends as a whole,
            # through Ctrl+Alt+Q, which the bridge turns into the teardown that
            # takes these processes down with it.  See CLAUDE.md, "Standing
            # rules".
            #
            # The window's own close is the same thing arriving by another road —
            # Alt+F4, the taskbar, the system menu — and it is asked of the
            # session rather than answered here (satellite.session_quit).
            if ev.type == pygame.QUIT:
                if quit_gesture(args.dashboard_cmd_file):
                    stop_event.set()
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                pointer.press(*ev.pos, win_w=win_w, win_h=win_h)
            elif ev.type == pygame.MOUSEMOTION:
                pointer.motion(*ev.pos, held=bool(ev.buttons[0]),
                               win_w=win_w, win_h=win_h)

        if paused_file is not None:
            session.set_paused(read_paused_state(paused_file, logger=logger))
        if command_file is not None:
            for cmd in consume_command_file(command_file, logger=logger, uppercase=False):
                apply_command(cmd, session, stop_event=stop_event, reload_playlist=_reload_playlist)

        session.advance()
        if status_writer is not None:
            status_writer.write(session)
        if hud is not None:
            # The clip on screen is the session's, not the published panel's — the
            # playlist walks on by itself between publishes — so the HUD is told what
            # is decoding, the same way Nau names its file from its own session.
            hud.tick(video=session.current_video.stem)

        if hud is not None and hud.display_suppressed:
            # Origenerator mode: the region is the hosted app's, so the player
            # goes black — an opaque frame over the video, under the HUD (whose
            # mode row is the way back).  Composited once per size, not per
            # tick: mpv holds an overlay until it is removed or replaced.
            if blackout_size != (win_w, win_h):
                blackout_size = (win_w, win_h)
                player.remove_overlay(_OV_SCRUBBER)
                player.remove_overlay(_OV_VOLUME)
                black = np.zeros((win_h, win_w, 4), dtype=np.uint8)
                black[:, :, 3] = 255  # opaque black; BGR stays zero
                player.overlay(_OV_BLACKOUT, 0, 0, black)
        else:
            if blackout_size is not None:
                blackout_size = None
                player.remove_overlay(_OV_BLACKOUT)
            scrubber = progress_bar_bgra(
                session.position_ms, session.duration_ms, None, win_w)
            player.overlay(_OV_SCRUBBER, 0, win_h - scrubber.shape[0], scrubber)
            vx, vy = chip_xy(win_w=win_w, win_h=win_h, timeline_h=TIMELINE_HEIGHT)
            player.overlay(_OV_VOLUME, vx, vy, volume_painter.bgra(volume.hud))

        clock.tick(60)

    session.close()
    pygame.quit()
    return 0
