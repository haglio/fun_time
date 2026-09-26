from __future__ import annotations

import logging
import os
import threading
from functools import partial
from pathlib import Path

import pygame
from app_support.win32 import set_app_user_model_id
from player_core.console_hud import ConsolePainter
from player_core.drive_gate import DriveGate
from player_core.file_channel import consume_command_file, read_paused_state
from player_core.mpv_player import MpvPlayer
from player_core.sdl_hints import deliver_the_focusing_click
from player_core.status import StatusWriter
from player_core.tcode import UdpTCodeSink
from player_core.tcode_driver import FunscriptTCodeDriver

from .cli import (
    audio_muted,
    build_parser,
    library_source,
    load_config,
    mode_memory,
    play_points,
    resolve_playlist,
)
from .clip_jumps import ClipJumps
from .clip_nav import ClipNav
from .controls import MainPlayerControls, apply_command
from .dashboard import Dashboard
from .display import Display
from .funscript_jumps import FunscriptJumps
from .input import Input
from .loading import LoadingCanceled, LoadingScreen
from .modes import Modes, reload_playlist
from .notice import NoticeWriter
from .overlay import HeatmapStrip, LoopThumbCapture
from .painter import HUD_OVERLAYS, ConsolePanel, Painter
from .player_window import take_outside_resizes
from .pointer import Pointer
from .published import Published
from .session import PlayerSession
from .status import status_fields
from .volume_control import VolumeControl

logger = logging.getLogger(__name__)

def _load_icon_surface(icon_path: Path | None):
    """The window icon Fun Time handed over as a pygame surface, or None."""
    if icon_path is None or not icon_path.exists():
        return None
    try:
        from PIL import Image  # noqa: PLC0415  (optional: no Pillow, no icon)
        img = Image.open(icon_path).convert("RGBA")
        return pygame.image.fromstring(img.tobytes(), img.size, "RGBA")
    except Exception:
        # Broad, because a window without its icon is still a window -- but
        # said, because a permanently missing icon otherwise reads in the log
        # exactly like one that loaded.
        logger.debug("No window icon: %s could not be read", icon_path,
                     exc_info=True)
        return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser({}).parse_args(argv)
    if args.config is not None:
        args = build_parser(load_config(args.config)).parse_args(argv)
    return _run(args)


def _set_aumid(taskbar_identity: str | None) -> None:
    """Claim this window's place on the taskbar, before there is a window.

    *taskbar_identity* is Fun Time's own: the main player is not an application the user
    launched but one window of the one they did, and it belongs on that button
    with the rest.
    """
    if not taskbar_identity:
        return
    try:
        set_app_user_model_id(taskbar_identity)
    except OSError:
        # A window on the wrong taskbar button is a launch that happened.
        logger.debug("No taskbar identity claimed", exc_info=True)



def _open_window(args):
    """Create the main player's window and return its surface.

    Comes before any library work: reading the library is the long part of
    startup, and until the window exists there is nowhere to say so — which is
    also why Fun Time, which waits on this window by caption, now finds it
    within its budget however cold the duration cache is.

    Borderless, like the satellites: the mode is on the in-video HUD, so the
    title bar would carry nothing.  With no chrome the client area is the whole
    rect Fun Time sizes it to, and the caption survives only for Alt-Tab and the
    window lookup.
    """
    # Before the window exists, and before pygame.init(): SDL otherwise eats the
    # click that focuses this window, so every press on the console has to be
    # made twice — once to wake the window, once to hit the button.  See
    # player_core.sdl_hints for the whole mechanism.
    deliver_the_focusing_click()
    pygame.init()
    if args.x is not None and args.y is not None:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{args.x},{args.y}"
    icon = _load_icon_surface(args.icon)
    if icon is not None:
        pygame.display.set_icon(icon)  # must precede set_mode to take effect
    screen = pygame.display.set_mode((args.width, args.height), pygame.NOFRAME)
    take_outside_resizes(pygame)
    pygame.display.set_caption("Main Player")
    return screen


def _status_writer(args, drive_gate, modes) -> StatusWriter:
    """The status file this player publishes.

    Every status carries the touch-down the trace chose for the boundary in
    play, so the arbiter ends Genau's turn where the picture drew it ending.
    The gate is asked for it as each status is written rather than when this is
    built: the choice is made while the frame is painted, and the writer
    publishes at its own throttled cadence in between; *modes* is asked the
    same way for the video's place in the library.
    """
    return StatusWriter(
        args.status_file,
        lambda session: status_fields(session, drive_gate.handoff_touch(),
                                      library=modes.library_status))


def _controls(session, stop_event, *, modes, jumps, funscript_jumps, volume,
              display, take_up_playlist) -> MainPlayerControls:
    """Every collaborator a command from the orchestrator can reach, bound once.

    Fun Time writes verbs into a file this player drains; which object answers
    which verb is wiring, and wiring does not change from one frame to the
    next, so it is said here rather than rebuilt around every command that
    arrives.
    """
    return MainPlayerControls(
        session=session,
        stop_event=stop_event,
        reload_playlist=take_up_playlist,
        modes=modes,
        jumps=jumps,
        funscript_jumps=funscript_jumps,
        set_volume_hud=volume.set,
        set_display=display.set_active,
    )


def _run(args) -> int:
    _set_aumid(args.taskbar_identity)
    screen = _open_window(args)
    # mpv renders the video directly into this window; overlays go on top.  Until
    # it does, the window is the loading screen's to paint.
    wid = pygame.display.get_wm_info()["window"]

    # The mode this player was last in.  Fun Time resumes the playlist a session
    # closed on rather than rebuilding it, so the mode that chose those videos is
    # last session's too — and a list of files cannot say which.
    memory = mode_memory(args)
    remembered = memory.read()

    loading = LoadingScreen(screen)
    try:
        # The long part of startup, and so the part the loading screen reports.
        # Fun Time passes --playlist and owns its selection; the source (when
        # present) powers version cycling, the length modes, and folding each
        # video's versions to a single rotation slot.
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

    clock = pygame.time.Clock()
    paused_file = args.paused_file
    command_file = args.command_file
    start_paused = read_paused_state(paused_file, logger=logger)

    player = MpvPlayer(wid, muted=audio_muted(args))
    session = PlayerSession(
        items,
        player=player,
        tcode=FunscriptTCodeDriver(UdpTCodeSink(args.tcode_host, args.tcode_port)),
        start_paused=start_paused,
        version_index=source.version_index if source is not None else None,
        play_points=play_points(args),
    )
    # Whether this window paints at all.  Fun Time gives the main slot's rect to
    # Genau in genau mode and minimizes the main player — minimized, so it keeps its taskbar
    # button — and says so on this channel; see main_player.display.
    display = Display(player, HUD_OVERLAYS)
    heatmap = HeatmapStrip()
    console_hud = ConsolePainter()
    # What Fun Time says about the main slot, and what Genau says it is doing
    # to the device.  Both arrive published, and a torn read keeps what was
    # there; see main_player.published.
    room = Published(args.console_file, args.drive_file)
    loop_thumbs = LoopThumbCapture()
    # What of Genau's publish this video's picture believes: the descent
    # forecasts it is holding, and whether Genau has been seen live here.  The
    # status writer below asks it for the touch it chose; the painter is what
    # makes it choose one.
    drive_gate = DriveGate(session)

    stop_event = threading.Event()
    # Every control on this HUD asks Fun Time rather than acting; so does
    # the close button.  See main_player.dashboard.
    dashboard = Dashboard(args.dashboard_cmd_file)
    # The main player's sound, as Fun Time publishes it.  The main player's own mpv is one
    # of two sinks it drives (Genau's clip audio is the other), so the level
    # here is drawn and reported, never decided; see main_player.volume_control.
    volume = VolumeControl(dashboard)


    # Clip navigation: a clip carved from a compilation records its siblings,
    # order, and source scene in its sidecar (see main_player.clip_nav). Built once over
    # the whole discovered library so "compilation"/"full video"/"clip jump" can
    # jump from whatever is on screen.  With no library there is nothing to jump
    # to, and an empty index answers that without a special case.
    entries = source.entries if source is not None else []
    clip_nav = ClipNav.build(
        [e.video for e in entries] + [c.video for c in (source.genau_clips if source else [])],
        source.metadata_root if source is not None else None,
    )
    notices = NoticeWriter(args.notice_file)
    jumps = ClipJumps(
        clip_nav, session, {e.video: e.funscript for e in entries}, notices,
    )
    # The funscript's own two moves — past this video's quiet stretch, or on to a
    # video that has scripting at all.  They need nothing but the session, since
    # the playlist already carries each video's funscript beside it.
    funscript_jumps = FunscriptJumps(session, notices)
    # Entering a compilation swaps the playlist in memory only, and Fun Time can
    # only rotate its resumed file onto a video the file contains — which a
    # compilation's clips often are not.  So the clip is remembered too, and
    # the compilation comes back around it rather than around whatever the list
    # leads with.
    jumps.resume(
        remembered.compilation,
        Path(remembered.video) if remembered.video else None,
    )
    # The length filter, the compilation and Fun Time's own narrowing, as the
    # console draws them and the memory keeps them.  See main_player.modes.
    modes = Modes(source, session, jumps, remembered=remembered.length_mode)
    status_writer = _status_writer(args, drive_gate, modes)
    # RELOAD_PLAYLIST: Fun Time owns the playlist file and rewrites it whenever
    # the room's selection changes.
    take_up_playlist = partial(
        reload_playlist, session, jumps, partial(resolve_playlist, args, source=source))
    pointer = Pointer(session, heatmap, volume, console_hud, dashboard)
    window_input = Input(pointer, dashboard)
    # What a verb from Fun Time reaches.  See main_player.controls for what each moves.
    controls = _controls(
        session, stop_event, modes=modes, jumps=jumps,
        funscript_jumps=funscript_jumps, volume=volume, display=display,
        take_up_playlist=take_up_playlist)
    # Everything this window draws on top of the video, and the order it goes up
    # in.  The console panel is its own part: it is where a frame reads the
    # outside world.  See main_player.painter.
    painter = Painter(
        player, session,
        ConsolePanel(session, room=room, drive_gate=drive_gate,
                     console_hud=console_hud, modes=modes),
        heatmap=heatmap, volume=volume, loop_thumbs=loop_thumbs)

    while not stop_event.is_set():
        win_w, win_h = pygame.display.get_window_size()
        window_input.deal(pygame.event.get(), win_w, win_h)

        session.set_paused(read_paused_state(paused_file, logger=logger))
        for cmd in consume_command_file(command_file, logger=logger, uppercase=False):
            apply_command(cmd, controls)

        session.advance()
        status_writer.write(session)

        # Write the mode down whenever it moves, whichever path moved it, so the
        # next session — which opens on this one's resumed playlist — can name it
        # and re-enter the compilation it was in.  Above the painting, because a
        # blanked main player still navigates: in genau mode the `[`/`]` keys drive it in
        # the background, and where they leave it is what the next session opens
        # on whether or not anyone was looking.
        memory.sync(modes.remembered)

        # Black while Genau owns the main slot's rect, and none of the work below
        # that builds a picture nobody can see.  Everything above still runs:
        # navigation, the funscript, the status file clipper_save reads.
        display.sync(win_w, win_h)
        if not display.active:
            clock.tick(60)
            continue

        player.tile_to_fill(win_w, win_h)
        player.push_still()
        painter.paint(win_w, win_h, hover=pointer.hover)

        clock.tick(60)

    session.close()
    pygame.quit()
    return 0
