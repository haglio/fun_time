"""Run loop for a native satellite player: a silent mpv window fun_time drives.

The satellite half of Nau's app shell, stripped to essentials — no funscript,
tcode, heatmap, record or version cycling.  mpv renders the video into a
pygame/SDL window; fun_time positions that window by HWND after launch and drives
playback through the command + paused files, reading back the status file.  The
one thing drawn on top is the lock HUD, composited into the video from the panel
fun_time publishes (see satellite.hud_overlay).

Not unit-tested: it needs the libmpv DLL and a real window.  The pure control
logic it drives lives in satellite.session / satellite.runtime / satellite.hud*,
tested against a fake player.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import pygame

from player_core.file_channel import consume_command_file, read_paused_state
from player_core.mpv_player import MpvPlayer
from player_core.status import StatusWriter

from .cli import audio_muted, build_parser, resolve_playlist
from .hud_overlay import HudOverlay
from .runtime import apply_command
from .session import SatelliteSession
from .status import status_fields

logger = logging.getLogger(__name__)

# Fun Time's own icon, so a satellite's Alt-Tab entry and taskbar button say
# which application it belongs to.  Without one, pygame supplies its own logo and
# these windows read as some unrelated program.  (Nau loads its icon the same
# way, in the genau repo.  The pair is not worth sharing through player_core:
# that package deliberately knows nothing of pygame — MpvPlayer takes a bare
# window handle — and an icon loader is not worth breaking that for.)
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
    pygame.init()
    if args.x is not None and args.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{args.x},{args.y}"
    # Borderless: the satellites replace VLC, which filled its whole slot with no
    # title bar, so the video has to fill the rect too.  mpv paints into this
    # window via its HWND (we never blit the surface); the sequencer then sizes
    # the window to the portrait/landscape rect, and with no chrome the client
    # area IS the rect.
    icon = _load_icon_surface()
    if icon is not None:
        pygame.display.set_icon(icon)  # must precede set_mode to take effect
    pygame.display.set_mode((args.width, args.height), pygame.NOFRAME)
    # fun_time passes a distinct --title per satellite ("Portrait AI Player" /
    # "Landscape AI Player") so the sequencer can resolve each window to its slot
    # by title when the pid lookup fails; a shared caption crosses the two.  It is
    # also what the window calls itself in Alt-Tab.
    pygame.display.set_caption(args.title)
    clock = pygame.time.Clock()
    # mpv renders the video directly into this window; the lock HUD is composited
    # on top of it through mpv, so the pygame surface itself is never blitted.
    wid = pygame.display.get_wm_info()["window"]

    paused_file: Path | None = args.paused_file
    command_file: Path | None = args.command_file
    start_paused = paused_file is not None and read_paused_state(paused_file, logger=logger)

    # loop_file=False so end-of-file advances the playlist; the lock toggles it on.
    # prefetch=True so mpv opens the next clip before the current ends and the
    # auto-advance is seamless instead of a cold on-screen reload.
    player = MpvPlayer(wid, muted=audio_muted(args), loop_file=False, prefetch=True)
    session = SatelliteSession(playlist, player=player, start_paused=start_paused)
    status_writer = StatusWriter(args.status_file, status_fields) if args.status_file else None
    # The lock HUD is composited into this window's video, so it needs no window
    # of its own and takes its clicks from this loop's own mouse events.
    hud = (
        HudOverlay(
            hud_file=args.hud_file, command_file=args.dashboard_cmd_file, player=player,
        )
        if args.hud_file and args.dashboard_cmd_file
        else None
    )
    stop_event = threading.Event()

    def _reload_playlist() -> None:
        reloaded = resolve_playlist(args)
        if reloaded:
            session.replace_playlist(reloaded)

    while not stop_event.is_set():
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                stop_event.set()
            elif (ev.type == pygame.KEYDOWN and ev.key == pygame.K_q
                  and ev.mod & pygame.KMOD_CTRL):
                stop_event.set()
            elif hud is not None and ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                hud.press(*ev.pos)
            elif hud is not None and ev.type == pygame.MOUSEMOTION:
                hud.motion(*ev.pos)

        if paused_file is not None:
            session.set_paused(read_paused_state(paused_file, logger=logger))
        if command_file is not None:
            for cmd in consume_command_file(command_file, logger=logger, uppercase=False):
                apply_command(cmd, session, stop_event=stop_event, reload_playlist=_reload_playlist)

        session.advance()
        if status_writer is not None:
            status_writer.write(session)
        if hud is not None:
            hud.tick()

        clock.tick(60)

    session.close()
    pygame.quit()
    return 0
