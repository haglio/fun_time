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
from player_core.timeline import TIMELINE_HEIGHT, progress_bar_bgra
from player_core.volume import VolumeHud, VolumeHudPainter, chip_xy

from .cli import audio_muted, build_parser, resolve_playlist
from .hud_overlay import HudOverlay
from .runtime import apply_command
from .session import SatelliteSession
from .status import status_fields

logger = logging.getLogger(__name__)

# Overlay ids the satellite composites, distinct from the lock HUD's (10).
_OV_SCRUBBER = 11
_OV_VOLUME = 12

# A satellite carries no audio, so its volume control is a fixed indicator: a
# muted speaker over an empty track, the same chip Nau draws so the players match.
_MUTED_INDICATOR = VolumeHud(volume=0, muted=True)

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
    # A satellite is never the focused window — the sequencer places every player
    # with SWP_NOACTIVATE and nothing afterwards activates one — so the click that
    # lands on a HUD button is also the click that activates the player.  SDL eats
    # that one by default: WIN_UpdateFocus records every button physically down as
    # the window takes focus (focus_click_pending), and WIN_CheckWParamMouseButton
    # then drops the press that follows unless SDL_MOUSE_FOCUS_CLICKTHROUGH is set.
    # That is the "click once to wake it, click again to do the thing" the HUD had:
    # the first press on a loop button was spent activating the window.  The hint
    # changes nothing about focus itself — Windows activates the window on that
    # click either way — only whether we are told about it.  SDL reads it straight
    # from the environment at click time; set here, before pygame.init(), so it is
    # in place ahead of any window (the same channel the position below uses).
    os.environ.setdefault("SDL_MOUSE_FOCUS_CLICKTHROUGH", "1")
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
    # The scrubber and volume indicator, drawn like Nau's from the shared engine —
    # a progress bar along the bottom and a muted volume chip at its right end.
    # The satellite has no funscript heatmap, no seek and no sound, so the bar is a
    # plain progress readout and the chip is a fixed muted indicator; both are here
    # so a satellite reads like the main player rather than as a bare video.
    volume_painter = VolumeHudPainter()
    stop_event = threading.Event()

    def _reload_playlist() -> None:
        reloaded = resolve_playlist(args)
        if reloaded:
            session.replace_playlist(reloaded)

    while not stop_event.is_set():
        for ev in pygame.event.get():
            # No key here ends this player.  A satellite is one of a set the
            # sequencer placed, and killing one alone leaves the session running
            # around a hole nothing refills — so the session ends as a whole or
            # not at all: Ctrl+Alt+Q, which the bridge turns into the teardown
            # that takes these processes down with it.  A Ctrl+Q handler used to
            # sit here and quit whichever satellite had focus; don't put it back.
            if ev.type == pygame.QUIT:
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
            # The clip on screen is the session's, not the published panel's — the
            # playlist walks on by itself between publishes — so the HUD is told what
            # is decoding, the same way Nau names its file from its own session.
            hud.tick(video=session.current_video.stem)

        # The scrubber (full window width, along the bottom) and the muted volume
        # indicator at its right end.  get_window_size reflects the slot the
        # sequencer moved this window into, so both track the real geometry.
        win_w, win_h = pygame.display.get_window_size()
        scrubber = progress_bar_bgra(
            session.position_ms, session.duration_ms, None, win_w)
        player.overlay(_OV_SCRUBBER, 0, win_h - scrubber.shape[0], scrubber)
        vx, vy = chip_xy(win_w=win_w, win_h=win_h, timeline_h=TIMELINE_HEIGHT)
        player.overlay(_OV_VOLUME, vx, vy, volume_painter.bgra(_MUTED_INDICATOR))

        clock.tick(60)

    session.close()
    pygame.quit()
    return 0
