"""The headset's room, off the headset: the session's three Funestras and
everything else that hangs there, each saying what it hangs, pumping its own
file channels and answering the pointer; :class:`TheRoom` is the one list they
are read off and :class:`Arrangement` the controllers' hold on them, frame by
frame.  The process around them is :mod:`fun_time_vr.app`.

``_pump_channels`` owns every file channel, since file I/O under a sync client
can stall for arbitrary milliseconds: the satellites' Funestras and the room's
panels on one worker, the Main Funestra on a worker of its own at Genau's
rate; each video paints on a thread and GL context of its own
(:mod:`fun_time_vr.video_thread`), since a clip change costs mpv whole frames.
"""
from __future__ import annotations

import configparser
import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
from app_support import ports
from app_support.file_channel import read_flag
from app_support.osr2_slot import Osr2Sender
from app_support.threading_utils import start_daemon_thread
from funestra.file_channel import append_command
from funestra.flick_folder import vr_flicks_in
from funestra.funestra import Funestra
from funestra.funestra_verbs import play_file
from funestra.genau_notifier import GenauNotifier
from funestra.modes import MainMode
from funestra.notices import NoticeWriter
from funestra.play_points import play_points_filename
from funestra.playlist import PlaylistItem, read_playlist
from funestra.pointer import OMNIPAUSE_TOGGLE
from funestra.tcode_driver import FunscriptTCodeDriver
from shared_ui.palette import BLUE, BORDER_DEFAULT, WHITE, as_fractions
from shared_ui.type_scale import SIZE_SMALL, in_pixels

from fun_time import preview_marker
from fun_time.dashboard_actions import (
    BROWSE_LIBRARY_CLOSE,
    LIBRARY_OPEN_FILENAME,
    REFERENCE_OPEN_FILENAME,
)
from fun_time.dashboard_osr2_model import Osr2PartReader
from fun_time.dashboard_runtime import load_dashboard_snapshot
from fun_time.event_log import NOTICE, SOURCE_MAIN, event_log_path, notice
from fun_time.funestra_status import read_genau_status, read_main_funestra_status
from fun_time.genau_config import GenauSettings
from fun_time.manifest import LaunchManifest
from fun_time.modes import scripted_item
from fun_time.session_handoff import headset_hold_asked
from fun_time.shared_state import BridgeState, read_shared_state, shared_state_path
from fun_time.unlogged_notices import UnloggedNotices
from main_funestra.cli import build_parser as build_main_funestra_parser
from main_funestra.cli import kino_on, library_source, load_config, mode_memory, resolve_playlist
from main_funestra.contract import MainChannels
from main_funestra.genau import ROBOT_HAND, GenauChannels
from main_funestra.kino import Kino
from satellite.contract import SatelliteChannels

from . import room
from .console_hud import DEG_PER_PX, HUD_WIDTH_PX, hud_hangs_from
from .controller_mesh import controllers_strip
from .cover import (
    COVER_DWELL_S,
    COVER_WIDTH_DEG,
    WEARER_STATUS,
    Cover,
    CoverAnchor,
    CoverDwell,
    CoverWatcher,
    SceneReady,
    paint_cover,
    scene_ready_file,
)
from .dash_panel import (
    DASH_DEG_PER_PX,
    DASH_WIDTH_PX,
    DashPointer,
    dash_height,
    paint_dash,
    with_the_osr2_part,
)
from .frame_look import FrameLook
from .gallery_panel import GalleryPanel
from .gallery_panel import hover_line as gallery_hover_line
from .gallery_panel import scroll_line as gallery_scroll_line
from .genau_in_the_headset import GenauInTheHeadset
from .headset_engine import HeadsetEngine
from .headset_verbs import HeadsetVerbs
from .headset_wear import HeadsetWear
from .hud_buttons import HudButtons, hud_button_names
from .hud_toggle import HUD_GAP_PAST_THE_TOGGLE_DEG
from .layout import (
    BANNER,
    CONSOLE,
    DASH,
    FUNESTRAS,
    GALLERY,
    LANDSCAPE,
    LAYOUT_FILENAME,
    LIBRARY,
    MAIN,
    PORTRAIT,
    REFERENCE,
    Layout,
    rearranged,
    remembered_layout,
    shown_at,
    widening,
    write_layout,
)
from .library_panel import (
    DISMISSED,
    LIBRARY_SIZE_PX,
    PICKED,
    LibraryHost,
    ShownWhileAsked,
    event_line,
    hover_line,
    open_line,
    scroll_from_stick,
    scroll_line,
    waiting_panel,
)
from .matrices import (
    fov_to_projection_matrix,
    pitch_rotation_matrix,
    pose_to_view_matrix,
    yaw_of_orientation,
    yaw_rotation_matrix,
)
from .notice_banner import banner_bgra, paint_banner
from .notices import NoticeBoard
from .perf import FramePerf
from .playback_watch import STALLED, PlaybackWatch
from .pointer import (
    DRAG,
    HANDLE_DEG,
    PRESS,
    RELEASE,
    SURFACE,
    Frame,
    HandInput,
    Pointer,
    PressEvent,
    Screen,
    carried_heading,
    cursor_vertices,
    handle_vertices,
    head_position,
    held_controllers,
    laser_vertices,
    surface_pixel,
)
from .projection import default_projection
from .projection_controls import ProjectionControls
from .reference_panel import (
    REFERENCE_WIDTH_DEG,
    REFERENCE_WIDTH_PX,
    ReferencePointer,
    paint_reference,
    reference_height,
)
from .render import (
    FrameTexture,
    RenderTarget,
    SceneRenderer,
    ScreenMesh,
    Wrap,
    angle_limits,
    immersive_wrap,
)
from .room import Hanging, Hangs
from .satellite_hud import HUD, hud_screen_name, screen_kind
from .scene import (
    MAIN_WIDTH_DEG,
    RADIUS_M,
    Placement,
    attached_below,
    attached_to,
    center_height,
    elevation_at,
    half_width,
    quad_layer_placement,
    surface_vertices,
    widened,
)
from .stacking import Stacking
from .surfaces import HudBitmap, LatestFrame
from .thumbs import Thumb, Thumbs, strongest
from .video_thread import VideoThread
from .wrap_readout import WrapReadout, readout

logger = logging.getLogger(__name__)

# Overlay ids shared with the desktop satellite (10 is its lock HUD).
_OV_NOTICE_BANNER = 13


# Longest texture side each video gets: near-native for the Main Funestra, and for
# a satellite's 28° of view well above what the headset resolves there.
MAIN_VIDEO_CAP_PX = 8192
SATELLITE_VIDEO_CAP_PX = 2048

LIBRARY_READING_SHOWN_AFTER_S = 0.3

# The file-channel worker's cadence: the dispatch loop polls these same files
# at ~20Hz, so 30Hz loses no responsiveness.
PUMP_HZ = 30.0
# The Main Funestra's own cadence, Genau's: a slower tick shows fewer of a
# flick's frames per cycle of the motion.
MAIN_TICK_HZ = 120.0

OFF_THE_HUD = (-1, -1)

LASER_REACH_M = 3.0  # when the laser meets no screen
HANDLE_COLOR = as_fractions(BORDER_DEFAULT, 0.35)
HOT_HANDLE_COLOR = as_fractions(BLUE, 0.85)
LASER_COLOR = as_fractions(WHITE, 0.55)
CURSOR_COLOR = as_fractions(WHITE, 0.9)
CONTROLLER_COLOR = as_fractions(BORDER_DEFAULT)


def _folders(spec: str) -> tuple[Path, ...]:
    return tuple(Path(part) for part in spec.split("|") if part.strip())


@dataclass(frozen=True)
class VrSettings:
    """The ``[vr]`` section the VR orchestrator adds to the launch manifest -- read
    here rather than in :mod:`fun_time.manifest`, which a desktop session writes."""

    library_dirs: tuple[Path, ...]
    audio_device: str
    compositor_layers: bool
    # Genau's role: its flicks folder, its companion's address, its engine's numbers.
    flicks_folder: Path | None = None
    notify_host: str = "127.0.0.1"
    notify_port: int = ports.AUDIO_COMPANION
    genau: GenauSettings = field(default_factory=GenauSettings)

    @classmethod
    def read(cls, path: Path) -> VrSettings:
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(str(path), encoding="utf-8")
        vr = parser["vr"]
        return cls(
            library_dirs=_folders(vr["library_dirs"]),
            audio_device=parser.get("vr", "audio_device", fallback=""),
            compositor_layers=parser.get(
                "vr", "compositor_layers", fallback="0").strip() == "1",
            flicks_folder=Path(vr["flicks_folder"]) if vr.get("flicks_folder") else None,
            notify_host=vr.get("notify_host", "127.0.0.1"),
            notify_port=int(vr.get("notify_port", ports.AUDIO_COMPANION)),
            genau=GenauSettings.from_manifest(vr),
        )


class _HangingScreen:
    def __init__(self, placement: Placement) -> None:
        self.placement = placement
        self.mesh: ScreenMesh | None = None
        self._hanging: tuple[Placement, float] | None = None

    @property
    def ready(self) -> bool:
        return self.mesh is not None and self.mesh.ready

    def rehang(self, aspect: float) -> None:
        self.rehang_at(self.placement, aspect)

    def rehang_at(self, placement: Placement, aspect: float) -> None:
        hanging = (placement, aspect)
        if hanging == self._hanging:
            return
        if self.mesh is None:
            self.mesh = ScreenMesh()
        self.mesh.upload(surface_vertices(placement, aspect=aspect))
        self._hanging = hanging

    def close(self) -> None:
        if self.mesh is not None:
            self.mesh.close()


class _VideoUnit:
    """What every Funestra here shares: a video thread, a texture target and a
    screen to hang it on."""

    SPOTS: dict[str, Placement] = {}

    def put_back(self) -> None:
        self.screen.placement = self.SPOTS[self.screen_name]

    def __init__(self, video: VideoThread, placement: Placement) -> None:
        self.video = video
        self.engine = video.engine
        self.target = RenderTarget()
        self.screen = _HangingScreen(placement)
        # Compositor-layer bookkeeping, render-thread-owned: whether the
        # target holds pixels its quad swapchain hasn't copied yet, and the
        # size the swapchain's content was copied at (None until the first
        # copy, and again after a resize makes the copied image stale).
        self.layer_dirty = False
        self.layer_rect: tuple[int, int] | None = None
        # Furniture last painted, pump-thread-owned.
        self._banner_shown: tuple | None = None
        self._audio_routed = False
        self._held = False

    def render_latest_frame(self) -> None:
        sized = (self.target.width, self.target.height)
        if self.video.show_newest(self.target):
            self.layer_dirty = True
            if (self.target.width, self.target.height) != sized:
                self.layer_rect = None
        if self.target.ready:
            self.screen.rehang_at(self.shown, self.target.aspect)

    @property
    def shown(self) -> Placement:
        return shown_at(self.screen_name, self.screen.placement, self.target.aspect)

    def layer_placement(self, scene_yaw_deg: float = 0.0, scene_pitch_deg: float = 0.0):
        """Pose and size for this screen's compositor quad, at the aspect its
        swapchain last copied; the scene angles turn and tilt the arrangement."""
        width, height = self.layer_rect
        aspect = width / height
        return quad_layer_placement(
            shown_at(self.screen_name, self.screen.placement, aspect), aspect=aspect,
            scene_yaw_deg=scene_yaw_deg, scene_pitch_deg=scene_pitch_deg,
        )

    def _window(self) -> tuple[int, int]:
        """The frame's own pixels, which is the window the Funestra places
        its HUD in; the HUD hangs beside the item, so only the
        placing's arithmetic sees these numbers."""
        return max(1, self.target.width), max(1, self.target.height)

    def route_audio(self) -> None:
        """Give the Funestra its sound on the first frame the headset is WORN:
        routed earlier, a parked endpoint takes the stream without consuming it
        and mpv's audio clock never ticks, freezing the Funestra on frame 1.
        When it wedges anyway, :mod:`fun_time_vr.playback_watch` notices."""
        if self._audio_routed:
            return
        self._audio_routed = True
        picked = self.engine.sound_goes_live(self._audio_device)
        if self._audio_device:
            logger.info("Audio device %r -> %s", self._audio_device, picked or "no match; default")

    def _press_on_the_hud(self, event: PressEvent) -> None:
        """A squeeze on the hanging HUD, placed by the Funestra the way a
        click on the desktop's is: in the window's pixels, at the HUD's own."""
        size = self.hud.size
        if size is None:
            return
        at = self.hud.in_the_window(*surface_pixel(event.u, event.v, size))
        if event.kind == PRESS:
            self._held = True
            self.funestra.press(*at, window=self._window())
        elif event.kind == DRAG:
            self.funestra.motion(*at, held=True, window=self._window())
        else:
            self._held = False
            self.funestra.release()

    def _hover_over_the_hud(self, hovered: tuple[str, tuple[float, float]] | None,
                            hud_screen: str) -> None:
        """Where the pointer rests on the HUD, for its tooltip -- off it
        entirely while a press is held, since the press's own drags say where
        the pointer is."""
        if self._held:
            return
        size = self.hud.size
        at = OFF_THE_HUD
        if hovered is not None and hovered[0] == hud_screen and size is not None:
            at = self.hud.in_the_window(*surface_pixel(*hovered[1], size))
        self.funestra.motion(*at, held=False, window=self._window())

    def _post(self, command: str) -> None:
        append_command(self._dashboard_cmd_file, command)

    def overlay_banner(self, notice) -> None:
        """Flash *notice* over this screen, or clear what was flashing,
        repainted only when it changes."""
        if not self.target.ready:
            return
        width, height = self.target.width, self.target.height
        shown = None if notice is None else (notice.message, notice.level, width, height)
        if shown == self._banner_shown:
            return
        self._banner_shown = shown
        if shown is None:
            self.engine.remove_overlay(_OV_NOTICE_BANNER)
            return
        placed = banner_bgra(notice.message, notice.level, width=width, height=height)
        if placed is None:
            return
        x, y, bgra = placed
        self.engine.overlay(_OV_NOTICE_BANNER, x, y, bgra)

    def pump(self, stop: threading.Event, now: float) -> None:
        """One turn of the file-channel worker — what every unit owes it."""
        raise NotImplementedError

    def close(self) -> None:  # what the frame loop's `finally` calls
        raise NotImplementedError

    def _close_graphics(self) -> None:
        self.target.close()
        self.screen.close()


def _in_the_slot(screen, frame, wrap: Wrap | None) -> tuple[Hanging, ...]:
    """Round the viewer rather than on a screen leaves no rectangle to aim at."""
    if wrap is not None:
        return (Hanging(Screen(MAIN, screen.placement, frame.aspect,
                               pressable=True, immersive=True),
                        frame=frame, wrap=wrap),)
    return (Hanging(_item_screen(MAIN, screen.placement, frame.aspect),
                    mesh=screen, frame=frame),)


def _item_screen(name: str, placement: Placement, aspect: float) -> Screen:
    by = widening(name, aspect)
    return Screen(name, widened(placement, by), aspect, movable=True, resizable=True,
                  pressable=True, item=True, widened_by=by)


def _metadata_root(manifest) -> Path | None:
    raw = manifest.regen.metadata_root.strip()
    return Path(raw) if raw else None


def _headsets_engine(contexts) -> Callable[[], HeadsetEngine]:
    return lambda: HeadsetEngine(contexts.get_proc_address, loop_file=False, prefetch=True)


def main_video(contexts, perf=None) -> VideoThread:
    return VideoThread(contexts, _headsets_engine(contexts), MAIN_VIDEO_CAP_PX,
                       name="main-video", perf=perf)


class _TheLibrary:
    """What Kino reads the library through, built the way the desktop's Main
    Funestra builds it: off the session's config, with the same files named."""

    def __init__(self, manifest: LaunchManifest, vr: VrSettings, *,
                 while_reading: Callable[[], None]) -> None:
        commands = manifest.commands
        config_path = Path(manifest.runtime.config_path)
        argv = ["--config", str(config_path), "--playlist", commands.main_funestra_playlist_file,
                "--state-dir", commands.state_dir, "--notice-file", commands.main_funestra_notice_file]
        if vr.flicks_folder is not None:
            argv += ["--flicks-dir", str(vr.flicks_folder)]
        metadata_root = _metadata_root(manifest)
        if metadata_root is not None:
            argv += ["--metadata-dir", str(metadata_root)]
        self.args = build_main_funestra_parser(load_config(config_path)).parse_args(argv)
        self.source = library_source(self.args, on_progress=lambda _phase: while_reading())
        self.memory = mode_memory(self.args)

    def playlist(self) -> list[PlaylistItem]:
        return resolve_playlist(self.args, source=self.source)

    def kino(self) -> Callable[[object], Kino]:
        return kino_on(self.args, source=self.source, memory=self.memory)


class _MainUnit(_VideoUnit):
    """The Main Funestra, with Kino and Genau running on it as they do on the
    desktop, and the headset's own verbs beside them."""

    screen_name = MAIN  # NOT `screen`, which every unit uses for its _HangingScreen
    SPOTS = {MAIN: Placement(0.0, 0.0, MAIN_WIDTH_DEG)}  # level and straight ahead

    def __init__(
        self, manifest: LaunchManifest, vr: VrSettings, contexts, *,
        remembered: Layout, stop: threading.Event, notices=None, perf=None,
        while_reading: Callable[[], None] = lambda: None,
    ) -> None:
        super().__init__(
            main_video(contexts, perf),
            remembered.placements.get(MAIN, self.SPOTS[MAIN]),
        )
        commands = manifest.commands
        self._dashboard_cmd_file = Path(commands.dashboard_cmd_file)
        self._audio_device = vr.audio_device.strip()
        self._notices = notices
        self._watch = PlaybackWatch()
        self._readout = WrapReadout()
        self._presses = _Presses(MAIN, CONSOLE)
        self._vr_dirs = () if vr.flicks_folder is None else (vr_flicks_in(vr.flicks_folder),)
        self.hud = HudBitmap(width=HUD_WIDTH_PX)
        self.users_frames = LatestFrame()
        self.users_frame_texture = FrameTexture()
        self._uploaded_frame = None
        self._flick_projection: tuple[Path | None, str] = (None, "")
        library = _TheLibrary(manifest, vr, while_reading=while_reading)
        self.verbs: HeadsetVerbs | None = None
        self.projection = ProjectionControls(verbs=lambda: self.verbs,
                                             showing=lambda: self.owns_the_slot)
        self.funestra = Funestra(
            self.engine,
            channels=MainChannels.from_manifest(commands),
            playlist=library.playlist(),
            tcode=FunscriptTCodeDriver(_osr2_slot(commands, MAIN)),
            locked=True, sound_is_the_rooms=True,
            users=_users(manifest, vr, stop,
                         kino=library.kino()),
            hud_surface=self.hud, users_frames=self.users_frames,
            window_verbs=self._apply_the_headsets_verb, host_controls=self.projection,
        )
        self.verbs = HeadsetVerbs(
            self.funestra.playback, metadata_root=_metadata_root(manifest),
            vr_dirs=tuple(vr.library_dirs), tilt_deg=remembered.tilt_deg)
        # A VR video nobody has chosen a projection for gets its frame read off
        # the thread that paints it: a fisheye circle found there opens it as a
        # fisheye rather than drawing its lower edge into a point as a 180 does.
        self.verbs.look_with(FrameLook(self.video))

    def _apply_the_headsets_verb(self, command: str) -> bool:
        """The Funestra asks after what runs on it; the verbs are built around
        the playback the Funestra makes, so they are reached through here."""
        return self.verbs.apply(command)

    @property
    def owns_the_slot(self) -> bool:
        """Whether the video is what fills the main slot, Genau's flick
        filling it otherwise."""
        return self.funestra.showing == MainMode.KINO

    @property
    def texture_in_the_slot(self) -> RenderTarget | FrameTexture:
        return self.target if self.owns_the_slot else self.users_frame_texture

    def hangings(self) -> tuple[Hanging, ...]:
        if self.owns_the_slot:
            if not self.target.ready:
                return ()
            return _in_the_slot(self.screen, self.target, self.verbs.wrap_of(self.target.video))
        if not self.users_frame_texture.ready:
            return ()
        return _in_the_slot(self.screen, self.users_frame_texture,
                            immersive_wrap(self._flick_projection[1]))

    @property
    def wraps_the_viewer(self) -> bool:
        return any(one.wrap is not None for one in self.hangings())

    @property
    def can_dial_the_wrap(self) -> bool:
        return self.owns_the_slot and angle_limits(self.verbs.projection) is not None

    @property
    def wrap_readout(self) -> str | None:
        if not self.owns_the_slot:
            return None
        return readout(self.verbs.projection, self.verbs.wrap)

    def banner_into_the_frame(self):
        """A banner drawn into a frame wrapped round the viewer lands on the seam
        between its two eyes; the floating one carries it instead."""
        if self._notices is None or self.wraps_the_viewer:
            return None
        return self._notices.banner(self.screen_name)

    def flash_the_readout(self, now: float) -> None:
        said = self._readout.frame(self.wrap_readout, now=now)
        if said is not None and self._notices is not None:
            self._notices.flash(said, level=NOTICE, screen=self.screen_name, now=now)

    def dial_the_wrap(self, *, stretch: float) -> None:
        if stretch != 1.0:
            self.verbs.set_height(self.verbs.wrap.height * stretch)

    def hangs_by(self) -> dict[str, Hangs]:
        return {MAIN: Hangs((self.screen,))}

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def render_latest_frame(self) -> None:
        super().render_latest_frame()
        self._show_the_users_frame()

    def _show_the_users_frame(self) -> None:
        """The frame the User in front put up -- Genau's, scrubbed to the OSR2
        -- uploaded untouched, and wrapped the way its flick is."""
        if self.owns_the_slot:
            return
        latest = self.users_frames.flick_frame
        if latest is None or latest.frame is None:
            return
        if latest.frame is not self._uploaded_frame:
            self.users_frame_texture.upload(latest.frame)
            self._uploaded_frame = latest.frame
            if latest.flick != self._flick_projection[0]:
                self._flick_projection = (
                    latest.flick, default_projection(str(latest.flick), self._vr_dirs))
        if self.users_frame_texture.ready:
            self.screen.rehang_at(
                shown_at(MAIN, self.screen.placement, self.users_frame_texture.aspect),
                self.users_frame_texture.aspect)

    def pump(self, stop: threading.Event, now: float) -> None:
        self._take_presses()
        self.funestra.tick(window=self._window())
        self.verbs.tick(now)
        if self.funestra.stopped:
            stop.set()
        self._watch_progress(now)
        if self._notices is not None:
            self.overlay_banner(self.banner_into_the_frame())

    def _take_presses(self) -> None:
        for event in self._presses.drain():
            if event.screen == CONSOLE:
                self._press_on_the_hud(event)
            elif event.kind == PRESS:
                self._post(OMNIPAUSE_TOGGLE)
        self._hover_over_the_hud(self._presses.hover, CONSOLE)

    def _watch_progress(self, now: float) -> None:
        """Say it out loud when the video stops advancing, and reopen it once:
        the failure leaves the Funestra unpaused, its duration known and its
        position frozen, raising nothing and logging nothing."""
        playback = self.funestra.playback
        verdict = self._watch.note(
            position_ms=playback.position_ms,
            playing=not playback.is_paused and playback.duration_ms > 0,
            now=now,
        )
        if verdict is None:
            return
        if verdict == STALLED:
            notice(logger, "the video stopped advancing; reopening it", source=SOURCE_MAIN,
                   level=logging.ERROR)
            self._reopen()
        else:
            notice(logger, "the video is still not advancing", source=SOURCE_MAIN,
                   level=logging.ERROR)

    def _reopen(self) -> None:
        """Load the video again and seek back to where it was -- the way out
        of a wedged pipeline, since mpv builds a whole new one, its audio
        output included."""
        playback = self.funestra.playback
        position_ms = playback.position_ms
        logger.warning("Reopening the Main Funestra at %.0fms", position_ms)
        playback.load(playback.index)
        if position_ms:
            playback.seek_to(position_ms)

    def close(self) -> None:
        self.verbs.close()  # before the Funestra: its looks read a frame from one
        self.video.close()  # frees mpv on the thread whose context it renders in
        self.funestra.close()  # what runs on it, and the line to the device
        self.users_frame_texture.close()
        self._close_graphics()


def _users(manifest: LaunchManifest, vr: VrSettings, stop: threading.Event, *, kino):
    """What runs on the Main Funestra: Kino, and Genau where the session names
    its flicks folder -- as the desktop's Main Funestra runs them."""
    users = {MainMode.KINO: kino}
    if vr.flicks_folder is None:
        return users
    commands = manifest.commands
    resumed = read_shared_state(shared_state_path(Path(commands.state_dir))) or BridgeState()
    users[MainMode.GENAU] = lambda _playback: GenauInTheHeadset(
        flicks_folder=vr.flicks_folder,
        settings=vr.genau,
        channels=GenauChannels(
            command=Path(commands.genau_cmd_file), paused=Path(commands.genau_paused_file),
            status=Path(commands.genau_status_file), drive=Path(commands.genau_drive_file)),
        notifier=GenauNotifier(vr.notify_host, vr.notify_port),
        tcode_sink=_osr2_slot(commands, ROBOT_HAND),
        notices=NoticeWriter(Path(commands.main_funestra_notice_file)),
        stop_event=stop,
        start_flick=read_genau_status(Path(commands.genau_status_file)).flick or None,
        latest=resumed.genau_latest,
        f_mode=resumed.genau_favorites_filter,
        orientations=(resumed.genau_plays_portrait, resumed.genau_plays_landscape),
        metadata_root=_metadata_root(manifest),
    )
    return users


class _SatelliteUnit(_VideoUnit):
    # Tuned on the first headset run: flush beside the main screen put both of them
    # in the peripheral vision.
    SPOTS = {
        LANDSCAPE: Placement(azimuth_deg=-38.0, elevation_deg=10.0, width_deg=28.0),
        PORTRAIT: Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0),
    }

    def __init__(
        self, funestra: str, manifest: LaunchManifest, contexts, *,
        vr: VrSettings, remembered: Mapping[str, Placement], notices=None, perf=None,
    ) -> None:
        super().__init__(
            VideoThread(contexts, _headsets_engine(contexts), SATELLITE_VIDEO_CAP_PX,
                        name=f"{funestra}-video", perf=perf),
            remembered.get(funestra, self.SPOTS[funestra]),
        )
        commands = manifest.commands
        self.screen_name = funestra  # its notices flash over its own frame
        self._notices = notices
        # What a launched satellite is given on its command line, read out of
        # the manifest instead: this one runs in the headset's own process.
        channels = SatelliteChannels.from_manifest(
            commands, funestra,
            play_points=Path(commands.state_dir) / play_points_filename(funestra))
        self._dashboard_cmd_file = channels.dashboard_cmd
        self._audio_device = vr.audio_device.strip()
        self.hud = HudBitmap()
        self.funestra = Funestra(
            self.engine, channels=channels, playlist=read_playlist(channels.playlist),
            tcode=FunscriptTCodeDriver(_osr2_slot(commands, funestra)),
            hud_surface=self.hud,
        )
        self.hud_texture = FrameTexture()
        self.hud_screen = _HangingScreen(self.screen.placement)
        self._hud_version = -1
        self._hud_shown = False
        self._buttons = HudButtons(funestra, post=self._post, texture=FrameTexture,
                                   mesh=lambda: _HangingScreen(self.screen.placement))
        self._presses = _Presses(funestra, hud_screen_name(funestra), *hud_button_names(funestra))

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def hangings(self) -> tuple[Hanging, ...]:
        if not self.target.ready:
            return ()
        frame = Hanging(
            _item_screen(self.screen_name, self.screen.placement, self.target.aspect),
            mesh=self.screen, frame=self.target)
        beside = self._buttons.hangings()
        if not self.hud_ready:
            return (frame, *beside)
        return (frame, *beside, Hanging(
            Screen(hud_screen_name(self.screen_name), self.hud_screen.placement,
                   self.hud_texture.aspect, pressable=True),
            mesh=self.hud_screen, frame=self.hud_texture, blend=True,
            forward_with=self.screen_name))

    def hangs_by(self) -> dict[str, Hangs]:
        return {self.screen_name: Hangs((self.screen,))}

    @property
    def hud_ready(self) -> bool:
        return self._hud_shown and self.hud_texture.ready and self.hud_screen.ready

    def render_latest_frame(self) -> None:
        super().render_latest_frame()
        rgba, version = self.hud.take()
        if version != self._hud_version:
            self._hud_version = version
            self._hud_shown = rgba is not None
            if rgba is not None:
                self.hud_texture.upload(rgba)
        minimized = self.funestra.hud_minimized
        if self.target.ready and (self._hud_shown or minimized):
            self._buttons.hang(self.funestra.outside_buttons, self.shown, self.target.aspect,
                               edge=self.funestra.hud_edge)
        else:
            self._buttons.unhang()
        if self._hud_shown and self.target.ready:
            self.hud_screen.placement = attached_to(
                self.funestra.hud_edge, self.shown, aspect=self.target.aspect,
                width_deg=self.hud_texture.width * DEG_PER_PX,
                hanging_aspect=self.hud_texture.aspect, gap_deg=HUD_GAP_PAST_THE_TOGGLE_DEG,
            )
            self.hud_screen.rehang(self.hud_texture.aspect)

    def pump(self, stop: threading.Event, now: float) -> None:
        self._take_presses()
        self.funestra.tick(window=self._window())
        if self._notices is not None:
            self.overlay_banner(self._notices.banner(self.screen_name))

    def _take_presses(self) -> None:
        for event in self._presses.drain():
            if self._buttons.take(event):
                continue
            if screen_kind(event.screen) == HUD:
                self._press_on_the_hud(event)
            elif event.kind == PRESS:
                self._post(OMNIPAUSE_TOGGLE)
        hovered = self._presses.hover
        self._buttons.point(None if hovered is None else hovered[0])
        self._hover_over_the_hud(hovered, hud_screen_name(self.screen_name))

    def close(self) -> None:
        self.video.close()  # frees mpv on the thread whose context it renders in
        self.funestra.close()
        self.hud_texture.close()
        self.hud_screen.close()
        self._buttons.close()
        self._close_graphics()


class _Presses:
    def __init__(self, *screens: str) -> None:
        self._screens = screens
        self._events: queue.SimpleQueue[PressEvent] = queue.SimpleQueue()
        self.hover: tuple[str, tuple[float, float]] | None = None

    def point(self, frame: Frame) -> None:
        for event in frame.events:
            if event.screen in self._screens:
                self._events.put(event)
        hover = frame.hover
        self.hover = (
            (hover.screen, (hover.u, hover.v))
            if hover is not None and hover.screen in self._screens and hover.handle == SURFACE
            else None
        )

    def drain(self) -> Iterator[PressEvent]:
        while True:
            try:
                yield self._events.get_nowait()
            except queue.Empty:
                return


def _upload(unit) -> bool:
    """Upload a painted panel if it changed, and say whether it can hang.  Its
    caller re-hangs it every frame all the same: rehanging only alongside an
    upload left a dragged panel's mesh where the drag started until a repaint."""
    with unit._lock:
        image = unit._image
    if image is not None and image is not unit._uploaded:
        unit.texture.upload(np.asarray(image))
        unit._uploaded = image
    return unit.texture.ready


class _ConsoleUnit:
    """The console the Main Funestra draws, hanging under whichever item
    fills the main slot -- or, while that item wraps the viewer and there is
    nothing to dock to, under the dashboard."""

    SPOTS: dict[str, Placement] = {}

    def __init__(self, main_unit: _MainUnit, dash) -> None:
        self._main_unit = main_unit
        self._dash = dash
        self._version = -1
        self._shown = False
        self.texture = FrameTexture()
        self.screen = _HangingScreen(main_unit.screen.placement)
        self._buttons = HudButtons(MAIN, post=main_unit._post, texture=FrameTexture,
                                   mesh=lambda: _HangingScreen(main_unit.screen.placement))
        self._presses = _Presses(*hud_button_names(MAIN))

    @property
    def in_the_room(self) -> bool:
        return not self._main_unit.funestra.hud_minimized

    def hangings(self) -> tuple[Hanging, ...]:
        beside = self._buttons.hangings()
        if not (self._shown and self.texture.ready):
            return beside
        return (*beside, Hanging(
            Screen(CONSOLE, self.screen.placement, self.texture.aspect, pressable=True),
            mesh=self.screen, frame=self.texture, blend=True, docked_to=MAIN))

    def hangs_by(self) -> dict[str, Hangs]:
        return {}  # it hangs from whatever is above it, and is never dragged

    def put_back(self) -> None:
        pass  # it follows whatever it is docked under, back to that screen's own spot

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)  # a squeeze on the console itself is the Main Funestra's

    def pump(self, stop: threading.Event, now: float) -> None:
        for event in self._presses.drain():
            self._buttons.take(event)
        hovered = self._presses.hover
        self._buttons.point(None if hovered is None else hovered[0])

    def render_latest_frame(self) -> None:
        rgba, version = self._main_unit.hud.take()
        if version != self._version:
            self._version = version
            self._shown = rgba is not None
            if rgba is not None:
                self.texture.upload(rgba)
        wrapped = self._main_unit.wraps_the_viewer
        slot_aspect = self._main_unit.texture_in_the_slot.aspect
        under, aspect = (
            (self._dash.screen.placement, self._dash.texture.aspect) if wrapped else
            (shown_at(MAIN, self._main_unit.screen.placement, slot_aspect), slot_aspect))
        edge = hud_hangs_from(self._main_unit.funestra.hud_edge, wrapped=wrapped)
        minimized = self._main_unit.funestra.hud_minimized
        if self._shown or minimized:
            self._buttons.hang(self._main_unit.funestra.outside_buttons, under, aspect, edge=edge,
                               with_sides=not wrapped)
        else:
            self._buttons.unhang()
        if not (self._shown and self.texture.ready):
            return
        self.screen.placement = attached_to(
            edge, under, aspect=aspect, width_deg=self.texture.width * DEG_PER_PX,
            hanging_aspect=self.texture.aspect, gap_deg=HUD_GAP_PAST_THE_TOGGLE_DEG,
        )
        self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()
        self._buttons.close()


_DASH_WIDTH_DEG = DASH_WIDTH_PX * DASH_DEG_PER_PX
_A_WIDE_VIDEO = 16 / 9


def _two_handles_over_the_main(width_deg: float) -> float:
    main = _MainUnit.SPOTS[MAIN]
    main_top = center_height(main) + half_width(main.width_deg) / _A_WIDE_VIDEO
    dash_drop = half_width(width_deg) * dash_height() / DASH_WIDTH_PX
    return elevation_at(main_top + 2 * RADIUS_M * math.radians(HANDLE_DEG) + dash_drop)


def _at_the_dashs_own_width(placement: Placement) -> Placement:
    return replace(placement, width_deg=_DASH_WIDTH_DEG)


class _DashUnit:
    # Painted like the console; the pair's handle while it carries one, and a spot for that.

    SPOTS = {
        DASH: Placement(azimuth_deg=0.0, elevation_deg=_two_handles_over_the_main(_DASH_WIDTH_DEG),
                        width_deg=_DASH_WIDTH_DEG),
        # Carrying the console, the video having wrapped the viewer: lower, under it.
        CONSOLE: Placement(azimuth_deg=0.0, elevation_deg=-11.0, width_deg=_DASH_WIDTH_DEG),
    }

    def __init__(self, main_unit: _MainUnit, *,
                 remembered: Mapping[str, Placement], dashboard_cmd_file: Path,
                 notices: NoticeBoard, dashboard_state_file: Path,
                 reference_flag: Path, state_dir: Path) -> None:
        self._main_unit = main_unit
        self._notices = notices
        self._state_file = dashboard_state_file
        self._reference_flag = reference_flag
        self._floating = _at_the_dashs_own_width(remembered.get(DASH, self.SPOTS[DASH]))
        self._wrapped = _at_the_dashs_own_width(remembered.get(CONSOLE, self.SPOTS[CONSOLE]))
        self._pointer = DashPointer(
            post=lambda command: append_command(dashboard_cmd_file, command))
        self._shown_as = preview_marker.shown_as()
        self._osr2 = Osr2PartReader(state_dir)
        self._presses = _Presses(DASH)
        self._lock = threading.Lock()
        self._bar_and_log = None
        self._painted = None
        self._image = None
        self._key = None
        self._uploaded = None
        self.texture = FrameTexture()
        self.screen = _HangingScreen(self._floating)

    @property
    def carrying_the_console(self) -> bool:  # a wrapped slot leaves it the only handle
        return self._main_unit.wraps_the_viewer

    @property
    def layout_key(self) -> str:  # which remembered spot a drag lands in
        return CONSOLE if self.carrying_the_console else DASH

    @property
    def placement(self) -> Placement:
        return self.screen.placement

    @placement.setter
    def placement(self, placement: Placement) -> None:
        self.screen.placement = placement
        if self.carrying_the_console:
            self._wrapped = placement
        else:
            self._floating = placement

    def put_back(self) -> None:
        self._floating, self._wrapped = self.SPOTS[DASH], self.SPOTS[CONSOLE]
        self.placement = self.SPOTS[self.layout_key]

    def hangings(self) -> tuple[Hanging, ...]:
        if not self.texture.ready:
            return ()
        return (Hanging(
            Screen(DASH, self.screen.placement, self.texture.aspect,
                   movable=True, pressable=True),
            mesh=self.screen, frame=self.texture, blend=True),)

    def hangs_by(self) -> dict[str, Hangs]:
        return {DASH: Hangs((self,), kept_as=lambda: self.layout_key)}

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def pump(self, stop: threading.Event, now: float) -> None:
        size = (DASH_WIDTH_PX, dash_height())
        for event in self._presses.drain():
            at = surface_pixel(event.u, event.v, size)
            if event.kind == PRESS:
                self._pointer.press(*at)
            elif event.kind == DRAG:
                self._pointer.drag(*at)
            elif event.kind == RELEASE:
                self._pointer.release()
        # The snapshot the desktop's own bar reads.
        snapshot = load_dashboard_snapshot(self._state_file)
        self._pointer.session_state(
            omni_paused=snapshot is not None and snapshot.omni_paused,
            voice_active=snapshot is None or snapshot.voice_active,
            f_mode=snapshot is not None and snapshot.f_mode,
            reference_open=read_flag(self._reference_flag, default=False),
            nothing_to_reset=snapshot is not None and snapshot.nothing_to_reset,
        )
        records = self._notices.records
        aim = self._presses.hover
        hover = surface_pixel(*aim[1], size) if aim is not None else None
        self._pointer.hover(hover)
        published = self._osr2.read()
        part = None if published is None else self._pointer.part.drawing(published)
        painted = (self._pointer.state, records, hover)
        if painted != self._painted:
            self._bar_and_log = paint_dash(*painted, shown_as=self._shown_as)
            self._painted = painted
        key = (painted, part)
        if key == self._key:
            return
        image = with_the_osr2_part(self._bar_and_log, part)
        with self._lock:
            self._image = image
        self._key = key

    def render_latest_frame(self) -> None:
        self.screen.placement = (
            self._wrapped if self.carrying_the_console else self._floating)
        if _upload(self):
            self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()


class _ReferenceUnit:
    """The hotkeys and voice reference, hanging from the dashboard while it is up."""

    SPOTS: dict[str, Placement] = {}

    def __init__(self, dash: _DashUnit, console: _ConsoleUnit, *, flag: Path) -> None:
        self._dash = dash
        self._console = console
        self._flag = flag
        self._pointer = ReferencePointer()
        self._presses = _Presses(REFERENCE)
        self._lock = threading.Lock()
        self._image = None
        self._key = None
        self._uploaded = None
        self.texture = FrameTexture()
        self.screen = _HangingScreen(dash.screen.placement)

    @property
    def showing(self) -> bool:
        return self._pointer.state.open

    def hangings(self) -> tuple[Hanging, ...]:
        if not (self.showing and self.texture.ready):
            return ()  # nothing to point at on a panel that is not there
        return (Hanging(
            Screen(REFERENCE, self.screen.placement, self.texture.aspect, pressable=True),
            mesh=self.screen, frame=self.texture, blend=True, forward_with=DASH),)

    def hangs_by(self) -> dict[str, Hangs]:
        return {}  # it moves with the dashboard it hangs from

    def put_back(self) -> None:
        pass  # it hangs under the dashboard, back to wherever that goes

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def pump(self, stop: threading.Event, now: float) -> None:
        size = (REFERENCE_WIDTH_PX, reference_height())
        for event in self._presses.drain():
            if event.kind == PRESS:
                self._pointer.press(*surface_pixel(event.u, event.v, size))
        self._pointer.showing(read_flag(self._flag, default=False))
        state = self._pointer.state
        if not state.open or state == self._key:
            return
        image = paint_reference(state)
        with self._lock:
            self._image = image
        self._key = state

    def render_latest_frame(self) -> None:
        if not _upload(self):
            return
        under = self._console if self._dash.carrying_the_console else self._dash
        self.screen.placement = attached_below(
            under.screen.placement, aspect=under.texture.aspect,
            width_deg=REFERENCE_WIDTH_DEG, hanging_aspect=self.texture.aspect)
        self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()


class _LibraryUnit:
    # A browse to read and press: dead ahead and wide, over the frame while it is up.
    SPOTS = {LIBRARY: Placement(azimuth_deg=0.0, elevation_deg=4.0, width_deg=80.0)}

    def __init__(
        self, *, remembered: Mapping[str, Placement], flag: Path, host,
        main_funestra_cmd_file: Path, main_funestra_status_file: Path,
        genau_cmd_file: Path, genau_status_file: Path, genau_has_the_slot: Callable[[], bool],
        dashboard_cmd_file: Path, metadata_root: Path | None,
    ) -> None:
        self._flag = flag
        self._host = host
        self._main_funestra_cmd_file = main_funestra_cmd_file
        self._metadata_root = metadata_root
        self._main_funestra_status_file = main_funestra_status_file
        self._genau_cmd_file = genau_cmd_file
        self._genau_status_file = genau_status_file
        self._genau_has_the_slot = genau_has_the_slot
        self._browsing_flicks = False
        self._dashboard_cmd_file = dashboard_cmd_file
        self._shown = ShownWhileAsked()
        self._presses = _Presses(LIBRARY)
        self._lock = threading.Lock()
        self._image = None
        self._uploaded = None
        self._token = 0
        self._opened_at = 0.0
        self._drawn = False
        self._hovered: tuple[int, int] | None = None
        self._scrolled = 0.0
        self.texture = FrameTexture()
        self.screen = _HangingScreen(remembered.get(LIBRARY, self.SPOTS[LIBRARY]))

    @property
    def showing(self) -> bool:
        return self._shown.showing and self._drawn

    @property
    def takes_the_stick(self) -> bool:
        return self.showing and self._presses.hover is not None

    def hangings(self) -> tuple[Hanging, ...]:
        if not (self.showing and self.texture.ready):
            return ()  # nothing to point at while it is put away
        return (Hanging(
            Screen(LIBRARY, self.screen.placement, self.texture.aspect,
                   movable=True, pressable=True),
            mesh=self.screen, frame=self.texture, blend=True, in_front=True),)

    def hangs_by(self) -> dict[str, Hangs]:
        return {LIBRARY: Hangs((self.screen,))}

    def put_back(self) -> None:
        self.screen.placement = self.SPOTS[LIBRARY]

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def scroll(self, notches: float) -> None:
        with self._lock:
            self._scrolled += notches

    def pump(self, stop: threading.Event, now: float) -> None:
        for answer in self._host.answers():
            said, _, video = answer.partition(" ")
            if said == PICKED and self._browsing_flicks:
                append_command(self._genau_cmd_file, play_file(PlaylistItem(Path(video))))
            elif said == PICKED:
                append_command(self._main_funestra_cmd_file,
                               play_file(scripted_item(video, self._metadata_root)))
            if said in (PICKED, DISMISSED):
                self._shown.put_away()
                append_command(self._dashboard_cmd_file, BROWSE_LIBRARY_CLOSE)
        if self._shown.asked(read_flag(self._flag, default=False)):
            self._token += 1
            self._opened_at = now
            self._drawn = False
            self._browsing_flicks = self._genau_has_the_slot()
            playing = (read_genau_status(self._genau_status_file).flick if self._browsing_flicks
                       else read_main_funestra_status(self._main_funestra_status_file).video)
            self._host.send(open_line(self._token, playing, flicks=self._browsing_flicks))
        events = list(self._presses.drain())
        if not self._shown.showing:
            return
        for event in events:
            self._host.send(event_line(event))
        self._send_the_pointer()
        self._show_what_it_drew(now)

    def _send_the_pointer(self) -> None:
        aim = self._presses.hover
        at = surface_pixel(*aim[1], LIBRARY_SIZE_PX) if aim is not None else None
        if at is not None and at != self._hovered:
            self._host.send(hover_line(*at))
        self._hovered = at
        with self._lock:
            notches = int(self._scrolled)
            self._scrolled -= notches
        if notches:
            self._host.send(scroll_line(notches))

    def _show_what_it_drew(self, now: float) -> None:
        frame = self._host.frame(self._token)
        if frame is not None:
            width, height, pixels = frame
            image = np.frombuffer(pixels, np.uint8).reshape(height, width, 4)
        elif not self._drawn and now - self._opened_at >= LIBRARY_READING_SHOWN_AFTER_S:
            image = waiting_panel()
        else:
            return
        with self._lock:
            self._image = image
        self._drawn = True

    def render_latest_frame(self) -> None:
        if _upload(self):
            self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self._host.close()
        self.texture.close()
        self.screen.close()


BANNER_ELEVATION_DEG = 24.0
BANNER_PX_PER_DEG = 40.0
# The family's small type as big as it reads on the console, drawn at the banner's density.
BANNER_FONT_PX = round(in_pixels(SIZE_SMALL) * BANNER_PX_PER_DEG * DEG_PER_PX)
BANNER_MAX_WIDTH_PX = 1600


class _BannerUnit:
    """The main screen's banner, floating in front of a frame wrapped round the viewer."""

    SPOTS: dict[str, Placement] = {}

    def __init__(self, main_unit, notices) -> None:
        self._main_unit = main_unit
        self._notices = notices
        self._lock = threading.Lock()
        self._image = None
        self._uploaded = None
        self._painted: tuple[str, int] | None = None
        self.texture = FrameTexture()
        self.screen = _HangingScreen(Placement(0.0, BANNER_ELEVATION_DEG, 1.0))

    def hangings(self) -> tuple[Hanging, ...]:
        if self._painted is None or not (self._main_unit.wraps_the_viewer and self.texture.ready):
            return ()
        return (Hanging(
            Screen(BANNER, self.screen.placement, self.texture.aspect),
            mesh=self.screen, frame=self.texture, blend=True, in_front=True),)

    def hangs_by(self) -> dict[str, Hangs]:
        return {}

    def put_back(self) -> None:
        pass

    def point(self, frame: Frame) -> None:
        pass

    def pump(self, stop: threading.Event, now: float) -> None:
        notice = self._notices.banner(MAIN) if self._main_unit.wraps_the_viewer else None
        painted = None if notice is None else (notice.message, notice.level)
        if painted == self._painted:
            return
        if painted is not None:
            image = paint_banner(
                notice.message, notice.level, max_width=BANNER_MAX_WIDTH_PX, size=BANNER_FONT_PX)
            self.screen.placement = Placement(
                0.0, BANNER_ELEVATION_DEG, image.width / BANNER_PX_PER_DEG)
            with self._lock:
                self._image = image
        self._painted = painted

    def render_latest_frame(self) -> None:
        if _upload(self):
            self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()


class _GalleryUnit:  # the hosted app's window
    SPOTS = {GALLERY: Placement(azimuth_deg=-100.0, elevation_deg=2.0, width_deg=95.0)}

    def __init__(self, *, remembered: Mapping[str, Placement], state_dir: Path,
                 shared_state_file: Path) -> None:
        self._panel = GalleryPanel(Path(state_dir))
        self._shared_state_file = shared_state_file
        self._presses = _Presses(GALLERY)
        self._lock = threading.Lock()
        self._image = None
        self._uploaded = None
        self._hovered: tuple[int, int] | None = None
        self._scrolled = 0.0
        self.showing = False
        self.texture = FrameTexture()
        self.screen = _HangingScreen(remembered.get(GALLERY, self.SPOTS[GALLERY]))

    @property
    def takes_the_stick(self) -> bool:
        return self.showing and self._presses.hover is not None

    def hangings(self) -> tuple[Hanging, ...]:
        if not (self.showing and self.texture.ready):
            return ()
        return (Hanging(
            Screen(GALLERY, self.screen.placement, self.texture.aspect,
                   movable=True, resizable=True, pressable=True, right_clicks=True),
            mesh=self.screen, frame=self.texture, blend=True),)

    def hangs_by(self) -> dict[str, Hangs]:
        return {GALLERY: Hangs((self.screen,))}

    def put_back(self) -> None:
        self.screen.placement = self.SPOTS[GALLERY]

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def scroll(self, notches: float) -> None:
        with self._lock:
            self._scrolled += notches

    def pump(self, stop: threading.Event, now: float) -> None:
        state = read_shared_state(self._shared_state_file)
        wanted = state is not None and state.origenerator_holds_a_satellite()
        events = list(self._presses.drain())
        self.showing = wanted
        if not wanted:
            self._hovered = None
            return
        for event in events:
            self._panel.press(event)
        self._send_the_pointer()
        frame = self._panel.frame(now)
        gone_quiet = self._panel.went_quiet(now)
        if gone_quiet is not None:
            logger.warning("%s", gone_quiet)
        if frame is None:
            return
        width, height, pixels = frame
        with self._lock:
            self._image = np.frombuffer(pixels, np.uint8).reshape(height, width, 4)

    def _send_the_pointer(self) -> None:
        aim = self._presses.hover
        size = self._panel.size
        at = surface_pixel(*aim[1], size) if aim is not None and size is not None else None
        if at is not None and at != self._hovered:
            self._panel.send(gallery_hover_line(*at))
        self._hovered = at
        with self._lock:
            notches = int(self._scrolled)
            self._scrolled -= notches
        if notches:
            self._panel.send(gallery_scroll_line(notches))

    def render_latest_frame(self) -> None:
        if _upload(self):
            self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self._panel.close()
        self.texture.close()
        self.screen.close()


class CoverUnit:  # :mod:`fun_time_vr.cover`, drawn in place of the scene
    SPOTS: dict[str, Placement] = {}

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self.holding = False  # render-thread-read, set on the pump's refresh
        self.awaiting_wearer = False  # set by the loop, read on the pump's refresh
        self.anchor_settled = False  # set by the loop: has it been read yet
        self._watcher = CoverWatcher(state_dir)
        self._lock = threading.Lock()
        self._pending: tuple[object, bool] | None = None  # (image, closing)
        self._cover: Cover | None = None
        self._reported = False
        self._uploaded = None
        self.texture = FrameTexture()
        self.screen = _HangingScreen(Placement(0.0, 0.0, COVER_WIDTH_DEG))
        # Render-thread-owned, read by the frame loop each frame.
        self.showing = False
        self.anchor = CoverAnchor()
        self.refresh()  # the loop's first renderable frame is already covered

    def hangings(self) -> tuple[Hanging, ...]:
        return ()  # it is drawn in place of the room, never as part of it

    def hangs_by(self) -> dict[str, Hangs]:
        return {}

    def put_back(self) -> None:
        pass  # it hangs where the viewer is looking, never in a remembered spot

    def point(self, frame: Frame) -> None:
        pass  # a cover stands in place of everything there is to aim at

    def closing_now(self) -> None:  # ending on its own: raise it unasked
        self._watcher.closing_now()

    @property
    def wanted(self) -> bool:  # a Funestra with no orchestrator never wants one
        with self._lock:
            return self._pending is not None

    @property
    def closing(self) -> bool:
        """Whether the painted cover is the teardown's.  Off the bitmap, not the
        texture, so a teardown holding its first kill is answered even where no
        frame was drawn."""
        with self._lock:
            return self._pending is not None and self._pending[1]

    def refresh(self) -> None:
        """Re-read the progress files and repaint if what they say has moved."""
        self.holding = headset_hold_asked(self._state_dir)
        cover = self._watcher.read()
        if cover is not None and not cover.closing and self.awaiting_wearer:
            cover = replace(cover, status=WEARER_STATUS)
        if cover == self._cover:
            return
        self._cover = cover
        with self._lock:
            self._pending = (paint_cover(cover), cover.closing) if cover is not None else None

    def pump(self, stop: threading.Event, now: float) -> None:
        self.refresh()

    def render_latest_frame(self) -> None:
        with self._lock:
            pending = self._pending
        if pending is None:
            self.showing = False
            self.anchor.release()  # the next hangs where the viewer is by then
            return
        image, _closing = pending
        if image is not self._uploaded:
            self.texture.upload(np.asarray(image))
            self._uploaded = image
            self.screen.rehang(self.texture.aspect)
        self.showing = self.texture.ready and self.screen.ready

    def settled(self) -> None:
        """The closing cover is as visible as it will get: its frame reached the
        compositor, or nothing is presented at all — a quit made with the
        headset off would else buy the timeout and no cover.
        """
        if not self.closing or self._reported:
            return
        self._reported = True
        try:
            self._watcher.ready_file.write_text("", encoding="utf-8")
        except OSError:
            logger.warning("Could not report the closing cover painted", exc_info=True)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()


class _LayoutKeeper:
    def __init__(self, path: Path, remembered: Layout) -> None:
        self._path = path
        self._placements = dict(remembered.placements)
        self._tilt_deg = remembered.tilt_deg
        self._lock = threading.Lock()
        self._unsaved = False
        self._settled = False

    def place(self, name: str, placement: Placement) -> None:
        with self._lock:
            self._placements[name] = placement
            self._unsaved = True

    def tilt(self, degrees: float, *, carried: bool) -> None:
        with self._lock:
            if degrees == self._tilt_deg:
                return
            self._tilt_deg = degrees
            self._unsaved = True
            self._settled = self._settled or not carried

    def forget(self) -> None:
        with self._lock:
            self._placements.clear()
            self._tilt_deg = 0.0
            self._unsaved = True

    def settle(self) -> None:
        with self._lock:
            self._settled = True

    def _write(self, *, settled_only: bool) -> None:
        with self._lock:
            if not (self._settled or (self._unsaved and not settled_only)):
                return
            snapshot = Layout(dict(self._placements), self._tilt_deg)
            self._settled = self._unsaved = False
        write_layout(self._path, snapshot)

    def pump(self, stop: threading.Event, now: float) -> None:
        self._write(settled_only=True)

    def close(self) -> None:
        self._write(settled_only=False)


class _ControllerPosts:
    def __init__(self, command_file: Path) -> None:
        self._command_file = command_file
        self._queue: queue.SimpleQueue[str] = queue.SimpleQueue()

    def post(self, commands: Sequence[str]) -> None:
        for command in commands:
            self._queue.put(command)

    def pump(self, stop: threading.Event | None, now: float) -> None:
        while True:
            try:
                command = self._queue.get_nowait()
            except queue.Empty:
                return
            append_command(self._command_file, command)

    def close(self) -> None:
        self.pump(None, 0.0)


class _PointerDrawing:
    def __init__(self) -> None:
        self._laser = ScreenMesh()
        self._cursor = ScreenMesh()
        self._handles = [ScreenMesh() for _ in range(3)]
        self._controllers = ScreenMesh()
        self._visible: list[tuple[ScreenMesh, tuple[float, float, float, float]]] = []
        self._holding = False

    def update(
        self, frame: Frame, screens: Sequence[Screen],
        held: Sequence[tuple[np.ndarray, np.ndarray]],
    ) -> None:
        self._visible = []
        if frame.hover is not None:
            screen = next((s for s in screens if s.name == frame.hover.screen), None)
            if screen is not None and screen.movable:
                strips = handle_vertices(
                    screen.placement, aspect=screen.aspect, resizable=screen.resizable)
                meshes = iter(self._handles)
                for kind, kind_strips in strips.items():
                    color = HOT_HANDLE_COLOR if frame.hover.handle == kind else HANDLE_COLOR
                    for strip in kind_strips:
                        mesh = next(meshes)
                        mesh.upload(strip)
                        self._visible.append((mesh, color))
        if frame.ray is not None:
            reach = frame.point.distance if frame.point is not None else LASER_REACH_M
            self._laser.upload(laser_vertices(frame.ray, length=reach))
            self._visible.append((self._laser, LASER_COLOR))
        if frame.point is not None and frame.hover is not None:
            self._cursor.upload(cursor_vertices(frame.point))
            self._visible.append((self._cursor, CURSOR_COLOR))
        controllers = controllers_strip(held)
        self._holding = len(controllers) > 0
        if self._holding:
            self._controllers.upload(controllers)

    def draw(self, renderer: SceneRenderer, view_proj: np.ndarray) -> None:
        for mesh, color in self._visible:
            renderer.draw_solid(mesh, view_proj, color)
        if self._holding:  # last: in the hand, so nearer than anything else in the scene
            renderer.draw_shaded(self._controllers, view_proj, CONTROLLER_COLOR)

    def close(self) -> None:
        for mesh in (self._laser, self._cursor, *self._handles, self._controllers):
            mesh.close()


def _unit_name(unit: object) -> str:
    screen = getattr(unit, "screen_name", "")
    return f"{type(unit).__name__}[{screen}]" if screen else type(unit).__name__


class _PumpFaults:  # one unit's pump failing, said once, not seven times a second
    def __init__(self) -> None:
        self._kind: dict[int, tuple[str, str]] = {}  # by identity: satellites share a class
        self._repeats: dict[int, int] = {}
        self._names: dict[int, str] = {}

    def failed(self, unit: object, exc: BaseException) -> None:
        key = id(unit)
        kind = (type(exc).__name__, str(exc))
        if self._kind.get(key) == kind:
            self._repeats[key] += 1
            return
        self._close_out(key)
        self._kind[key] = kind
        self._repeats[key] = 0
        self._names[key] = _unit_name(unit)
        logger.error("%s.pump failed", self._names[key], exc_info=exc)

    def worked(self, unit: object) -> None:
        key = id(unit)
        if key in self._kind:
            self._close_out(key)
            del self._kind[key]
            self._repeats.pop(key, None)

    def _close_out(self, key: int) -> None:
        if self._repeats.get(key):
            logger.error(
                "...and %d more %s.pump failures like it: %s",
                self._repeats[key], self._names[key], self._kind[key][1],
            )


def _pump_channels(units: list, stop: threading.Event, perf: FramePerf,
                   hz: float = PUMP_HZ) -> None:
    """The file-channel worker: every unit's flags, drains, status writes and
    repaints — file I/O that can stall under a sync client, so never the frame
    loop's thread.  Two threads on one mpv is its design; see funestra.mpv_gate.
    Guarded per unit: the OSR2 is driven from here (:class:`_PumpFaults`)."""
    period = 1.0 / hz
    faults = _PumpFaults()
    while not stop.is_set():
        started = time.monotonic()
        for unit in units:
            try:
                unit.pump(stop, started)
            except Exception as exc:  # noqa: BLE001 - the whole point is that none escapes
                faults.failed(unit, exc)
            else:
                faults.worked(unit)
        perf.note("pump", (time.monotonic() - started) * 1e3)
        perf.maybe_flush()
        stop.wait(max(0.0, period - (time.monotonic() - started)))


def _scene_rotation(scene_yaw: float, tilt_deg: float) -> np.ndarray:
    return yaw_rotation_matrix(scene_yaw) @ pitch_rotation_matrix(math.radians(tilt_deg))


def _close_channels(units: list, *, keep=None) -> None:
    """Close everything the worker was pumping, all but *keep* — guarded per
    unit as :func:`_pump_channels` is, since what follows it waits on it."""
    for unit in units:
        if unit is keep:
            continue
        try:
            unit.close()
        except Exception:  # noqa: BLE001 - the whole point is that none escapes
            logger.warning("%s.close failed", type(unit).__name__, exc_info=True)


class _SaysWhenItFirstMoves:  # places the OSR2's first move against the rest of the log
    def __init__(self, sink, role: str) -> None:
        self._sink = sink
        self._role = role
        self._said = False

    def want(self, wanted: bool) -> None:
        self._sink.want(wanted)

    def send(self, command: str) -> None:
        if not self._said:
            self._said = True
            logger.info("First T-Code from the %s role: %s", self._role, command)
        self._sink.send(command)

    def close(self) -> None:
        self._sink.close()


def _osr2_slot(commands, sender: str) -> _SaysWhenItFirstMoves:
    return _SaysWhenItFirstMoves(Osr2Sender(sender, port=int(commands.osr2_slot_port)), sender)


def _still_blank(main_unit, satellites: Sequence, console) -> tuple[str, ...]:
    """Which screens the session opens with have yet to RENDER a frame, as
    against merely being sized (``has_frame``, never ``ready``); the main slot
    counts once.  Named, so the reveal can say which one it gave up on."""
    frames = [(main_unit.screen_name, main_unit.texture_in_the_slot)]
    frames += [(satellite.screen_name, satellite.target) for satellite in satellites]
    if console.in_the_room:
        frames.append((CONSOLE, console.texture))
    return tuple(name for name, frame in frames if not frame.has_frame)


def _hands_for_the_funestras(
    library: _LibraryUnit, gallery: _GalleryUnit, hands: Mapping[str, HandInput], *,
    elapsed_s: float,
) -> Mapping[str, HandInput]:
    scrolling = next((unit for unit in (library, gallery) if unit.takes_the_stick), None)
    if scrolling is None:
        return hands
    scrolling.scroll(scroll_from_stick(
        strongest(hand.stick_y for hand in hands.values()), elapsed_s))
    return {name: replace(hand, stick_x=0.0, stick_y=0.0) for name, hand in hands.items()}


class TheRoom:
    """Everything the headset hangs in front of him, and the one list each is read off."""

    def __init__(
        self, *, main_unit: _MainUnit, satellites: Sequence[_SatelliteUnit], dash: _DashUnit,
        console: _ConsoleUnit, reference: _ReferenceUnit, library: _LibraryUnit,
        banner: _BannerUnit, gallery: _GalleryUnit, cover: CoverUnit, notices: NoticeBoard,
        keeper: _LayoutKeeper, posts: _ControllerPosts, pointing: _PointerDrawing,
        cover_watch: CoverWatch, stop: threading.Event,
    ) -> None:
        self.main_unit = main_unit
        self.satellites = tuple(satellites)
        self.console = console
        self.library = library
        self.gallery = gallery
        self.cover = cover
        self.keeper = keeper
        self.posts = posts
        self.pointing = pointing
        self.cover_watch = cover_watch
        self.stop = stop
        self.wear = HeadsetWear()
        # Dash before the console, which hangs off where the dashboard ended up.
        self.units = [main_unit, *self.satellites, dash, console, reference, library,
                      banner, gallery, cover]
        self.closing = [notices, *self.units, keeper, posts]
        self.pumped = [one for one in self.closing if one is not main_unit]
        self.where = room.where_they_hang(self.units)

    @property
    def videos(self) -> tuple[_VideoUnit, ...]:
        return (self.main_unit, *self.satellites)

    def start_pumping(self, perf: FramePerf) -> tuple[threading.Thread, threading.Thread]:
        return (
            start_daemon_thread(
                target=_pump_channels, args=(self.pumped, self.stop, perf), name="file-channels"),
            start_daemon_thread(
                target=_pump_channels, args=([self.main_unit], self.stop, perf),
                kwargs={"hz": MAIN_TICK_HZ}, name="main-funestra"),
        )

    def note_the_wearer(self, worn: bool, *, now: float) -> None:
        self.posts.post(self.wear.said(worn, now=now))

    def render_latest_frames(self) -> None:
        for unit in self.units:
            unit.render_latest_frame()

    def still_blank(self) -> tuple[str, ...]:
        return _still_blank(self.main_unit, self.satellites, self.console)

    def route_audio(self) -> None:
        for unit in self.videos:
            unit.route_audio()

    def hangings(self) -> list[Hanging]:
        return room.what_hangs(self.units)

    def close(self, *, keep_the_cover: bool) -> None:
        _close_channels(self.closing, keep=self.cover if keep_the_cover else None)


def hang_the_room(
    manifest: LaunchManifest, vr: VrSettings, contexts, *, manifest_path: Path,
    cover: CoverUnit, perf: FramePerf | None = None,
    present_the_cover: Callable[[], object] = lambda: None,
) -> TheRoom:
    commands = manifest.commands
    state_dir = Path(commands.dashboard_cmd_file).parent
    remembered = remembered_layout(state_dir)
    stop = threading.Event()
    # One read of the event log per tick, pumped before every screen's banner.
    notices = NoticeBoard(event_log_path(state_dir), unlogged=UnloggedNotices(state_dir))
    main_unit = _MainUnit(
        manifest, vr, contexts, remembered=remembered, stop=stop, notices=notices, perf=perf,
        while_reading=present_the_cover)
    present_the_cover()  # the Main Funestra has opened its media, and the satellites are next
    satellites = [
        _SatelliteUnit(funestra, manifest, contexts, vr=vr, remembered=remembered.placements,
                       notices=notices, perf=perf)
        for funestra in (PORTRAIT, LANDSCAPE)
    ]
    present_the_cover()
    reference_flag = state_dir / REFERENCE_OPEN_FILENAME
    dash = _DashUnit(
        main_unit,
        remembered=remembered.placements,
        dashboard_cmd_file=Path(commands.dashboard_cmd_file),
        notices=notices,
        dashboard_state_file=Path(commands.dashboard_state_file),
        reference_flag=reference_flag,
        state_dir=Path(commands.state_dir),
    )
    console = _ConsoleUnit(main_unit, dash)
    library = _LibraryUnit(
        remembered=remembered.placements,
        flag=state_dir / LIBRARY_OPEN_FILENAME,
        host=LibraryHost(manifest_path=manifest_path, state_dir=state_dir),
        main_funestra_cmd_file=Path(commands.main_funestra_cmd_file),
        main_funestra_status_file=Path(commands.main_funestra_status_file),
        genau_cmd_file=Path(commands.genau_cmd_file),
        genau_status_file=Path(commands.genau_status_file),
        genau_has_the_slot=lambda: not main_unit.owns_the_slot,
        dashboard_cmd_file=Path(commands.dashboard_cmd_file),
        metadata_root=_metadata_root(manifest),
    )
    return TheRoom(
        main_unit=main_unit, satellites=satellites, dash=dash, console=console,
        reference=_ReferenceUnit(dash, console, flag=reference_flag), library=library,
        banner=_BannerUnit(main_unit, notices),
        gallery=_GalleryUnit(remembered=remembered.placements, state_dir=state_dir,
                             shared_state_file=shared_state_path(state_dir)),
        cover=cover, notices=notices,
        keeper=_LayoutKeeper(state_dir / LAYOUT_FILENAME, remembered),
        posts=_ControllerPosts(Path(commands.dashboard_cmd_file)),
        pointing=_PointerDrawing(),
        cover_watch=CoverWatch(cover, SceneReady(scene_ready_file(state_dir))),
        stop=stop,
    )


class CoverWatch:
    """How long the cover has been in front of the eyes, and of a wearer's."""

    def __init__(self, cover: CoverUnit, scene_ready: SceneReady, *,
                 dwell_s: float = COVER_DWELL_S) -> None:
        self._cover = cover
        self._scene_ready = scene_ready
        self._on_screen = CoverDwell(dwell_s=dwell_s)
        self._worn = CoverDwell(dwell_s=dwell_s)
        self._up_at: float | None = None
        self._down_said = False

    def frame_began(self, *, blank: Sequence[str], focused: bool, views_tracked: bool) -> None:
        self._cover.anchor_settled = self._worn.dwelt and views_tracked
        self._cover.awaiting_wearer = not blank and not focused  # set before he looks
        self._scene_ready.note(blank=blank, read=self._on_screen.dwelt)

    def drawing(self, now: float) -> None:
        if self._up_at is None:
            self._up_at = now
            logger.info("Cover drawing to the headset")

    def frame_ended(self, *, covered: bool, focused: bool, should_render: bool,
                    now: float) -> None:
        self._on_screen.note(covered)
        self._worn.note(covered and focused)
        if self._up_at is not None and not covered and not self._down_said:
            self._down_said = True
            logger.info("Cover down after %.1fs on screen", now - self._up_at)
        if covered or not should_render:
            self._cover.settled()


@dataclass(frozen=True)
class Arranged:
    hangings: list[Hanging]
    screens: list[Screen]  # back to front, as the eyes draw them
    scene_rotation: np.ndarray
    scene_yaw: float
    scene_pitch_deg: float


class Arrangement:
    """The controllers' hold on the room, frame by frame."""

    def __init__(self, the_room: TheRoom, *, pointer: Pointer | None = None) -> None:
        self._room = the_room
        self._pointer = pointer or Pointer()
        self._thumbs = Thumbs()
        self._stacking = Stacking()
        # The recentering yaw, with the role's tilt read in beside it each frame.
        self.scene_yaw = 0.0
        self.scene_rotation = np.eye(4, dtype=np.float32)

    def arrange(self, hands: Mapping[str, HandInput], views, *, elapsed_s: float,
                now: float) -> Arranged:
        the_room = self._room
        main_unit = the_room.main_unit
        verbs = main_unit.verbs
        if verbs.recenter.take():
            self.scene_yaw = yaw_of_orientation(orientation_of(views[0]))
            logger.info("Recentered the scene onto heading %.0f°", math.degrees(self.scene_yaw))
        reset = verbs.layout_reset.take()
        if reset:
            self._pointer.let_go()
            logger.info("Put the Funestras and the dashboard back in their default spots and sizes")
        self.scene_rotation = _scene_rotation(self.scene_yaw, verbs.tilt_deg)
        hangings = the_room.hangings()
        screens = room.arranged(self._stacking, hangings)
        head = head_position([_position_of(view) for view in views])
        frame = self._pointer.frame(
            hands, head=head, scene_rotation=self.scene_rotation, screens=screens)
        if frame.taken is not None:
            self._stacking.take(frame.taken)
        dialing = main_unit.can_dial_the_wrap
        thumb = self._thumbs.frame(
            _hands_for_the_funestras(the_room.library, the_room.gallery, hands, elapsed_s=elapsed_s),
            self._pointer, elapsed_s=elapsed_s, dialing=dialing)
        the_room.posts.post(thumb.commands)
        if dialing:
            main_unit.dial_the_wrap(stretch=thumb.stretch)
        main_unit.flash_the_readout(now)
        self.scene_yaw, lift_deg = carried_heading(self.scene_yaw, frame.carried)
        verbs.nudge_tilt(lift_deg)
        scene_pitch_deg = verbs.tilt_deg
        self.scene_rotation = _scene_rotation(self.scene_yaw, scene_pitch_deg)
        self._move(frame, thumb, reset=reset, lifted=bool(lift_deg), tilt_deg=scene_pitch_deg)
        for unit in the_room.units:
            unit.point(frame)
        the_room.pointing.update(frame, screens, held_controllers(
            hands, head=head, scene_rotation=self.scene_rotation))
        return Arranged(hangings, screens, self.scene_rotation, self.scene_yaw, scene_pitch_deg)

    def _move(self, frame: Frame, thumb: Thumb, *, reset: bool, lifted: bool,
              tilt_deg: float) -> None:
        the_room = self._room
        funestras = {name: frame.moved.get(name, the_room.where[name].placement)
                     for name in FUNESTRAS}
        moved = frame.moved | rearranged(funestras, grow=thumb.grow, nearer_by=thumb.nearer)
        for name, placement in moved.items():
            the_room.where[name].put(placement)  # its own spot, of however many
            the_room.keeper.place(the_room.where[name].spot(name), placement)
        the_room.keeper.tilt(tilt_deg, carried=lifted)
        if reset:
            for unit in the_room.units:
                unit.put_back()
            the_room.keeper.forget()
        if frame.settled or thumb.settled or reset:
            the_room.keeper.settle()


def orientation_of(view) -> tuple[float, float, float, float]:
    orientation = view.pose.orientation
    return (orientation.x, orientation.y, orientation.z, orientation.w)


def _position_of(view) -> tuple[float, float, float]:
    position = view.pose.position
    return (position.x, position.y, position.z)


def draw_eyes(
    session,
    renderer: SceneRenderer,
    hangings: Sequence[Hanging],
    pointing: _PointerDrawing,
    views,
    scene_rotation: np.ndarray,
    *,
    screens: Sequence[Screen],
    as_quads: set[str],
) -> None:
    """Render the projection layer's two eyes: a video wrapping the viewer, then
    *screens* back to front, less those the compositor took as quads, then the
    pointer's chrome over all of it.  *scene_rotation* is where they all sit."""
    wrap = room.wrapped(hangings)
    flat = room.drawn(hangings, screens=screens, as_quads=as_quads)
    for eye_index, view in enumerate(views):
        session.bind_eye_framebuffer(eye_index)
        renderer.begin_eye()
        projection_matrix = fov_to_projection_matrix(
            view.fov.angle_left, view.fov.angle_right,
            view.fov.angle_up, view.fov.angle_down,
            0.05, 100.0,
        )
        # Rotation only: the scene must not parallax with head
        # translation, or a projected sphere swims.
        view_matrix = pose_to_view_matrix((0.0, 0.0, 0.0), orientation_of(view))
        view_proj = projection_matrix @ view_matrix @ scene_rotation
        view_proj32 = np.ascontiguousarray(view_proj, dtype=np.float32)
        if wrap is not None:
            inv32 = np.ascontiguousarray(np.linalg.inv(view_proj), dtype=np.float32)
            renderer.draw_immersive(wrap.wrap, wrap.frame.texture, inv32, eye_index)
        for mesh, texture, blend in flat:
            renderer.draw_screen(mesh, texture, view_proj32, blend=blend)
        pointing.draw(renderer, view_proj32)
        session.release_eye_framebuffer(eye_index)
