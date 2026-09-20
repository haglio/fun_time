"""Run loop for a native satellite player: an mpv window fun_time drives.

The satellite half of the main player's app shell, stripped to essentials — no funscript,
tcode, heatmap, record or version cycling.  mpv renders the video into a
pygame/SDL window; fun_time positions that window by HWND after launch and drives
playback through the command + paused files, reading back the status file.  Three
things are composited on top: the lock HUD from the panel fun_time publishes, the
scrubber and the volume chip — they and the picture take this loop's mouse events.

A shell: the control logic it drives lives in satellite.session,
satellite.runtime, satellite.pointer, satellite.volume and
player_core.satellite_hud*, and the loop itself runs against fakes for the
window system and the video engine (tests/test_satellite_app_loop.py).
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import pygame
from app_support.win32 import set_app_user_model_id
from player_core.file_channel import consume_command_file, read_paused_state
from player_core.mpv_player import MpvPlayer
from player_core.playhead import PlayheadHudPainter, readout_xy, video_playhead
from player_core.sdl_hints import deliver_the_focusing_click
from player_core.session_quit import quit_gesture
from player_core.status import StatusWriter
from player_core.timeline import TIMELINE_HEIGHT, progress_bar_bgra
from player_core.volume import VolumeHudPainter, chip_xy

from main_player.play_points import PlayPoints

from .cli import audio_muted, build_parser, resolve_playlist
from .hud_overlay import HudOverlay
from .pointer import Pointer
from .runtime import SatelliteControls, apply_command
from .session import SatelliteSession
from .status import status_fields
from .volume import SatelliteVolume

logger = logging.getLogger(__name__)

# Overlay ids, over the lock HUD's 10: mpv draws them in ascending order.
_OV_SCRUBBER = 11
_OV_VOLUME = 12
_OV_READOUT = 13

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


def _open_window(args) -> int:
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
    if args.taskbar_identity:
        try:
            set_app_user_model_id(args.taskbar_identity)
        except OSError:
            logger.info("Could not take the taskbar identity %s", args.taskbar_identity)
    pygame.init()
    if args.x is not None and args.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{args.x},{args.y}"
    icon = _load_icon_surface()
    if icon is not None:
        pygame.display.set_icon(icon)  # must precede set_mode to take effect
    # Borderless, so the client area IS the slot: mpv paints into this window via
    # its HWND (the pygame surface is never blitted) and the sequencer sizes it to
    # the portrait/landscape rect.
    pygame.display.set_mode((args.width, args.height), pygame.NOFRAME)
    # A distinct --title per satellite, so the sequencer can resolve each window
    # to its slot by title when the pid lookup fails; also its Alt-Tab name.
    pygame.display.set_caption(args.title)
    return pygame.display.get_wm_info()["window"]


@dataclass(frozen=True)
class _Runtime:
    """Everything the frame loop drives, built once around the open window."""

    player: MpvPlayer
    session: SatelliteSession
    pointer: Pointer
    controls: SatelliteControls
    stop_event: threading.Event
    volume: SatelliteVolume
    volume_painter: VolumeHudPainter
    readout_painter: PlayheadHudPainter
    paused_file: Path | None
    command_file: Path | None
    dashboard_cmd_file: Path | None
    status_writer: StatusWriter | None
    hud: HudOverlay | None


def _build_runtime(args, wid: int, playlist: list[Path]) -> _Runtime:
    paused_file: Path | None = args.paused_file
    start_paused = paused_file is not None and read_paused_state(paused_file, logger=logger)
    # loop_file=False so end-of-file advances the playlist; the lock toggles it on.
    # prefetch=True so mpv opens the next clip before the current ends and the
    # auto-advance is seamless instead of a cold on-screen reload.
    # muted=True: a satellite is heard only once its chip is asked (satellite.volume).
    player = MpvPlayer(wid, muted=True, loop_file=False, prefetch=True)
    session = SatelliteSession(playlist, player=player, start_paused=start_paused,
                               play_points=PlayPoints(args.play_points_file))
    stop_event = threading.Event()

    def _reload_playlist() -> None:
        reloaded = resolve_playlist(args)
        if reloaded:
            session.replace_playlist(reloaded)

    # Composited into this window's video, so it needs no window of its own.
    hud = (
        HudOverlay(
            hud_file=args.hud_file, command_file=args.dashboard_cmd_file, player=player,
        )
        if args.hud_file and args.dashboard_cmd_file
        else None
    )
    volume = SatelliteVolume(player, live=not audio_muted(args))
    return _Runtime(
        player=player,
        session=session,
        pointer=Pointer(session=session, volume=volume, hud=hud,
                        dashboard_cmd_file=args.dashboard_cmd_file),
        controls=SatelliteControls(
            session=session, stop_event=stop_event, reload_playlist=_reload_playlist),
        stop_event=stop_event,
        volume=volume,
        volume_painter=VolumeHudPainter(),
        readout_painter=PlayheadHudPainter(),
        paused_file=paused_file,
        command_file=args.command_file,
        dashboard_cmd_file=args.dashboard_cmd_file,
        status_writer=StatusWriter(args.status_file, status_fields) if args.status_file else None,
        hud=hud,
    )


def _take_events(runtime: _Runtime, win_w: int, win_h: int) -> None:
    """This pass's window events.  No key here ends this player: the session ends
    as a whole, through Ctrl+Alt+Q, which the bridge turns into the teardown that
    takes these processes down with it (CLAUDE.md, "Standing rules").  The
    window's own close is that same ask; see player_core.session_quit."""
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT:
            if quit_gesture(runtime.dashboard_cmd_file):
                runtime.stop_event.set()
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            runtime.pointer.press(*ev.pos, win_w=win_w, win_h=win_h)
        elif ev.type == pygame.MOUSEMOTION:
            runtime.pointer.motion(*ev.pos, held=bool(ev.buttons[0]),
                                   win_w=win_w, win_h=win_h)


def _paint_overlays(runtime: _Runtime, win_w: int, win_h: int) -> None:
    """The scrubber, the volume chip and the playhead pill over this frame."""
    session, player = runtime.session, runtime.player
    if session.showing_picture:
        player.remove_overlay(_OV_SCRUBBER)
    else:
        scrubber = progress_bar_bgra(session.position_ms, session.duration_ms, None, win_w)
        player.overlay(_OV_SCRUBBER, 0, win_h - scrubber.shape[0], scrubber)
    vx, vy = chip_xy(win_w=win_w, win_h=win_h, timeline_h=TIMELINE_HEIGHT)
    player.overlay(_OV_VOLUME, vx, vy, runtime.volume_painter.bgra(runtime.volume.hud))
    readout = video_playhead(session.position_ms, session.duration_ms, player.frame_rate)
    if readout is None:
        player.remove_overlay(_OV_READOUT)
    else:
        pill = runtime.readout_painter.bgra(readout)
        player.overlay(_OV_READOUT, *readout_xy(
            pill.shape[1], win_w=win_w, win_h=win_h, timeline_h=TIMELINE_HEIGHT), pill)


def _run(args, playlist: list[Path]) -> int:
    runtime = _build_runtime(args, _open_window(args), playlist)
    clock = pygame.time.Clock()
    while not runtime.stop_event.is_set():
        # Before the events, which have to be placed against the window they
        # landed in; the sequencer can move this one between passes.
        win_w, win_h = pygame.display.get_window_size()
        _take_events(runtime, win_w, win_h)

        if runtime.paused_file is not None:
            runtime.session.set_paused(read_paused_state(runtime.paused_file, logger=logger))
        if runtime.command_file is not None:
            for cmd in consume_command_file(runtime.command_file, logger=logger, uppercase=False):
                apply_command(cmd, runtime.controls)

        runtime.session.advance()
        runtime.player.push_still()
        if runtime.status_writer is not None:
            runtime.status_writer.write(runtime.session)
        if runtime.hud is not None:
            # The clip on screen is the session's, not the published panel's — the
            # playlist walks on by itself between publishes — so the HUD is told what
            # is decoding, the same way the main player names its file from its own session.
            runtime.hud.tick(video=runtime.session.name_on_screen,
                             playback_speed=runtime.session.speed)

        _paint_overlays(runtime, win_w, win_h)
        clock.tick(60)

    runtime.session.close()
    pygame.quit()
    return 0
