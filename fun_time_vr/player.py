"""The VR player process: the session's three Funestras composited into one
OpenXR scene.

The desktop session runs the Main Funestra and the two satellites as processes
owning windows; an OpenXR runtime gives the headset to one rendering process, so
in VR all three are player_core Funestras in this one, each playing through
:class:`fun_time_vr.headset_player.HeadsetPlayer` into a texture.  Kino and
Genau run on the Main Funestra as they do on the desktop, with the headset's
own verbs beside them (:mod:`fun_time_vr.headset_verbs`); each Funestra draws
its panel on a bitmap that hangs beside its picture and hands a User's own
picture over as a frame (:mod:`fun_time_vr.surfaces`), and the controllers move
and resize every screen (:mod:`fun_time_vr.pointer`).

``_pump_channels`` owns every file channel, since file I/O under a sync client
can stall for arbitrary milliseconds: the satellites' Funestras and the room's
panels on one worker, the Main Funestra on a worker of its own at Genau's
rate; each video paints on a thread and GL context of its own
(:mod:`fun_time_vr.video_thread`), since a clip change costs mpv whole frames.
The frame loop only copies each newest picture into the scene and hands the
compositor its layers.  With ``vr.compositor_layers=true`` flat screens go as
quad layers instead; the cost is in docs/known-issues.md.  Both ends of a
session are covered from here (:mod:`fun_time_vr.cover`), the headset having
no monitors for the desktop's overlay windows.

A shell: what it wires is tested outside it, per CLAUDE.md's standing rules.
"""
from __future__ import annotations

import argparse
import configparser
import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path

import numpy as np
from app_support import ports
from app_support.file_channel import read_flag
from app_support.logging_utils import install_exception_logging
from app_support.threading_utils import start_daemon_thread
from app_support.win32 import set_app_user_model_id
from player_core.clip_folder import vr_clips_in
from player_core.file_channel import append_command
from player_core.funestra import Channels, Funestra
from player_core.genau_notifier import GenauNotifier
from player_core.modes import MainMode
from player_core.play_points import play_points_filename
from player_core.player_verbs import play_file
from player_core.playlist import PlaylistItem, read_playlist
from player_core.pointer import OMNIPAUSE_TOGGLE
from player_core.tcode import UdpTCodeSink
from player_core.tcode_driver import FunscriptTCodeDriver

from fun_time import preview_marker
from fun_time.dashboard_actions import (
    BROWSE_LIBRARY_CLOSE,
    LIBRARY_OPEN_FILENAME,
    REFERENCE_OPEN_FILENAME,
)
from fun_time.dashboard_runtime import load_dashboard_snapshot
from fun_time.event_log import NOTICE, SOURCE_MAIN, EventLogHandler, event_log_path, notice
from fun_time.genau_config import GenauSettings
from fun_time.manifest import LaunchManifest
from fun_time.modes import scripted_item
from fun_time.player_status import read_genau_status, read_main_player_status
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.session_handoff import (
    headset_hold_asked,
    headset_hold_stops_the_runtime,
    report_the_headset_held,
)
from fun_time.shared_state import read_shared_state, shared_state_path
from fun_time.unlogged_notices import UnloggedNotices
from main_player.cli import build_parser as build_main_player_parser
from main_player.cli import library_source, load_config, mode_memory, resolve_playlist
from main_player.clip_nav import ClipNav
from main_player.genau import GenauChannels
from main_player.kino import Kino
from main_player.notice import NoticeWriter
from satellite.contract import SatelliteChannels

from . import room, vr_runtime
from .bringup import open_vr_session
from .console_panel import DEG_PER_PX, PANEL_WIDTH_PX, panel_hangs_from
from .controller_mesh import controllers_strip
from .cover import (
    COVER_CLEAR,
    COVER_WIDTH_DEG,
    WEARER_STATUS,
    Cover,
    CoverAnchor,
    CoverSeen,
    CoverWatcher,
    SceneReady,
    paint_cover,
    scene_ready_file,
)
from .dash_panel import DASH_WIDTH_PX, DashPointer, dash_height, paint_dash
from .genau_in_the_headset import GenauInTheHeadset
from .headset_player import HeadsetPlayer
from .headset_verbs import HeadsetVerbs
from .headset_wear import HeadsetWear
from .layout import (
    BANNER,
    DASH,
    LANDSCAPE,
    LAYOUT_FILENAME,
    LIBRARY,
    MAIN,
    PANEL,
    PLAYERS,
    PORTRAIT,
    REFERENCE,
    Layout,
    migrate_layout,
    read_layout,
    rearranged,
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
from .picture_look import PictureLook
from .playback_watch import STALLED, PlaybackWatch
from .pointer import (
    DRAG,
    PRESS,
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
from .reference_panel import (
    REFERENCE_WIDTH_DEG,
    REFERENCE_WIDTH_PX,
    ReferencePointer,
    paint_reference,
    reference_height,
)
from .render import FrameTexture, RenderTarget, SceneRenderer, ScreenMesh, Wrap, immersive_wrap
from .room import Hanging, Hangs
from .satellite_hud import HUD, HUD_GAP_DEG, hud_screen_name, screen_kind
from .scene import (
    MAIN_WIDTH_DEG,
    Placement,
    attached_below,
    attached_to,
    quad_layer_placement,
    surface_vertices,
    widened,
)
from .scheduling import ahead_of_background_work
from .stacking import Stacking
from .surfaces import LatestPicture, PanelBitmap
from .thumbs import Thumbs, strongest
from .video_thread import VideoThread
from .wrap_readout import WrapReadout, readout

logger = logging.getLogger(__name__)

# Overlay ids shared with the desktop satellite (10 is its lock HUD).
_OV_NOTICE_BANNER = 13


# Longest texture side each video gets: near-native for the main player, and for
# a satellite's 28° of view well above what the headset resolves there.
MAIN_VIDEO_CAP_PX = 8192
SATELLITE_VIDEO_CAP_PX = 2048

LIBRARY_READING_SHOWN_AFTER_S = 0.3

# The file-channel worker's cadence: the dispatch loop polls these same files
# at ~20Hz, so 30Hz loses no responsiveness.
PUMP_HZ = 30.0
# The Main Funestra's own cadence, Genau's: a slower tick shows fewer of a
# clip's frames per cycle of the motion.
MAIN_TICK_HZ = 120.0

OFF_THE_PANEL = (-1, -1)

LASER_REACH_M = 3.0  # when the laser meets no screen
HANDLE_COLOR = (0.85, 0.85, 0.9, 0.35)
HOT_HANDLE_COLOR = (0.3, 0.6, 1.0, 0.85)
LASER_COLOR = (1.0, 1.0, 1.0, 0.55)
CURSOR_COLOR = (1.0, 1.0, 1.0, 0.9)
CONTROLLER_COLOR = (0.8, 0.8, 0.85, 1.0)


def _show_error_popup(message: str) -> None:
    """Say why the headset never lit up -- a hidden launch that just exits
    is indistinguishable from a crash."""
    # Qt loads only for this: a headset session draws through OpenXR.
    from shared_ui.alert import show_alert  # noqa: PLC0415

    show_alert("FunTimeVR", message,
               icon=preview_marker.icon_file(PROJECT_VR_ICON, preview_marker.shown_as()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FunTimeVR player (one OpenXR scene, three players)")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="The windows bridge manifest INI the orchestrator wrote")
    return parser


def _folders(spec: str) -> tuple[Path, ...]:
    return tuple(Path(part) for part in spec.split("|") if part.strip())


@dataclass(frozen=True)
class VrSettings:
    """The ``[vr]`` section the VR orchestrator adds to the launch manifest -- read
    here rather than in :mod:`fun_time.manifest`, which a desktop session writes."""

    tcode_udp_host: str
    tcode_udp_port: int
    library_dirs: tuple[Path, ...]
    audio_device: str
    compositor_layers: bool
    # Genau's role: its clips folder, its companion's address, its engine's numbers.
    clips_folder: Path | None = None
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
            tcode_udp_host=vr["tcode_udp_host"],
            tcode_udp_port=int(vr["tcode_udp_port"]),
            library_dirs=_folders(vr["library_dirs"]),
            audio_device=parser.get("vr", "audio_device", fallback=""),
            compositor_layers=parser.get(
                "vr", "compositor_layers", fallback="0").strip() == "1",
            clips_folder=Path(vr["clips_folder"]) if vr.get("clips_folder") else None,
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
        self.player = video.player
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
        """The picture's own pixels, which is the window the Funestra places
        its panel in; the panel hangs beside the picture, so only the
        placing's arithmetic sees these numbers."""
        return max(1, self.target.width), max(1, self.target.height)

    def route_audio(self) -> None:
        """Give the player its sound on the first frame the headset is WORN:
        routed earlier, a parked endpoint takes the stream without consuming it
        and mpv's audio clock never ticks, freezing the player on frame 1.
        When it wedges anyway, :mod:`fun_time_vr.playback_watch` notices."""
        if self._audio_routed:
            return
        self._audio_routed = True
        picked = self.player.sound_goes_live(self._audio_device)
        if self._audio_device:
            logger.info("Audio device %r -> %s", self._audio_device, picked or "no match; default")

    def _press_on_the_panel(self, event: PressEvent) -> None:
        """A squeeze on the hanging panel, placed by the Funestra the way a
        click on the desktop's is: in the window's pixels, at the panel's own."""
        size = self.panel.size
        if size is None:
            return
        at = self.panel.in_the_window(*surface_pixel(event.u, event.v, size))
        if event.kind == PRESS:
            self._held = True
            self.funestra.press(*at, window=self._window())
        elif event.kind == DRAG:
            self.funestra.motion(*at, held=True, window=self._window())
        else:
            self._held = False
            self.funestra.release()

    def _hover_over_the_panel(self, hovered: tuple[str, tuple[float, float]] | None,
                              panel_screen: str) -> None:
        """Where the pointer rests on the panel, for its tooltip -- off it
        entirely while a press is held, since the press's own drags say where
        the pointer is."""
        if self._held:
            return
        size = self.panel.size
        at = OFF_THE_PANEL
        if hovered is not None and hovered[0] == panel_screen and size is not None:
            at = self.panel.in_the_window(*surface_pixel(*hovered[1], size))
        self.funestra.motion(*at, held=False, window=self._window())

    def _post(self, command: str) -> None:
        append_command(self._dashboard_cmd_file, command)

    def overlay_banner(self, notice) -> None:
        """Flash *notice* over this picture, or clear what was flashing,
        repainted only when it changes."""
        if not self.target.ready:
            return
        width, height = self.target.width, self.target.height
        shown = None if notice is None else (notice.message, notice.level, width, height)
        if shown == self._banner_shown:
            return
        self._banner_shown = shown
        if shown is None:
            self.player.remove_overlay(_OV_NOTICE_BANNER)
            return
        placed = banner_bgra(notice.message, notice.level, width=width, height=height)
        if placed is None:
            return
        x, y, bgra = placed
        self.player.overlay(_OV_NOTICE_BANNER, x, y, bgra)

    def pump(self, stop: threading.Event, now: float) -> None:
        """One turn of the file-channel worker — what every unit owes it."""
        raise NotImplementedError

    def close(self) -> None:  # what the frame loop's `finally` calls
        raise NotImplementedError

    def _close_graphics(self) -> None:
        self.target.close()
        self.screen.close()


def _wrap_of(verbs, video: str | None) -> Wrap | None:
    return immersive_wrap(
        verbs.projection_of(video), fov_deg=verbs.fov_of(video), height=verbs.height_of(video))


def _in_the_slot(screen, picture, wrap: Wrap | None) -> tuple[Hanging, ...]:
    """Round the viewer rather than on a screen leaves no rectangle to aim at."""
    if wrap is not None:
        return (Hanging(Screen(MAIN, screen.placement, picture.aspect,
                               pressable=True, immersive=True),
                        picture=picture, wrap=wrap),)
    return (Hanging(_picture_screen(MAIN, screen.placement, picture.aspect),
                    mesh=screen, picture=picture),)


def _picture_screen(name: str, placement: Placement, aspect: float) -> Screen:
    by = widening(name, aspect)
    return Screen(name, widened(placement, by), aspect, movable=True, resizable=True,
                  pressable=True, picture=True, widened_by=by)


def _metadata_root(manifest) -> Path | None:
    raw = manifest.regen.metadata_root.strip()
    return Path(raw) if raw else None


def _headsets_player(contexts) -> Callable[[], HeadsetPlayer]:
    return lambda: HeadsetPlayer(contexts.get_proc_address, loop_file=False, prefetch=True)


def main_video(contexts, perf=None) -> VideoThread:
    return VideoThread(contexts, _headsets_player(contexts), MAIN_VIDEO_CAP_PX,
                       name="main-video", perf=perf)


class _TheLibrary:
    """What Kino reads the library through, built the way the desktop's Main
    Player builds it: off the session's config, with the same files named."""

    def __init__(self, manifest: LaunchManifest, vr: VrSettings, *,
                 while_reading: Callable[[], None]) -> None:
        commands = manifest.commands
        config_path = Path(manifest.runtime.config_path)
        argv = ["--config", str(config_path), "--playlist", commands.main_player_playlist_file,
                "--state-dir", commands.state_dir]
        if vr.clips_folder is not None:
            argv += ["--clips-dir", str(vr.clips_folder)]
        metadata_root = _metadata_root(manifest)
        if metadata_root is not None:
            argv += ["--metadata-dir", str(metadata_root)]
        self.args = build_main_player_parser(load_config(config_path)).parse_args(argv)
        self.source = library_source(self.args, on_progress=lambda *_: while_reading())
        self.memory = mode_memory(self.args)

    def playlist(self) -> list[PlaylistItem]:
        return resolve_playlist(self.args, source=self.source)

    def kino(self, notice_file: Path) -> Callable[[object], Kino]:
        source = self.source
        entries = source.entries if source is not None else []
        clip_nav = ClipNav.build(
            [entry.video for entry in entries] + [
                clip.video for clip in (source.genau_clips if source is not None else [])],
            source.metadata_root if source is not None else None,
        )
        return partial(
            Kino, source=source, clip_nav=clip_nav, notices=NoticeWriter(notice_file),
            memory=self.memory, remembered=self.memory.read(),
            resolve_playlist=self.playlist)


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
        self._presses = _Presses(MAIN, PANEL)
        self._vr_dirs = () if vr.clips_folder is None else (vr_clips_in(vr.clips_folder),)
        self.panel = PanelBitmap(width=PANEL_WIDTH_PX)
        self.users_picture = LatestPicture()
        self.users_picture_texture = FrameTexture()
        self._uploaded_frame = None
        self._clip_projection: tuple[Path | None, str] = (None, "")
        library = _TheLibrary(manifest, vr, while_reading=while_reading)
        self.verbs: HeadsetVerbs | None = None
        self.funestra = Funestra(
            self.player,
            channels=Channels(
                playlist=Path(commands.main_player_playlist_file),
                command=Path(commands.main_player_cmd_file),
                paused=Path(commands.main_player_paused_file),
                status=Path(commands.main_player_status_file),
                play_points=Path(commands.state_dir) / play_points_filename("main_player"),
                console=Path(commands.main_player_console_file),
                dashboard_cmd=self._dashboard_cmd_file,
                drive=Path(commands.genau_drive_file),
            ),
            playlist=library.playlist(),
            tcode=FunscriptTCodeDriver(_SaysWhenItFirstMoves(
                UdpTCodeSink(vr.tcode_udp_host, vr.tcode_udp_port), "main")),
            locked=True, sound_is_the_rooms=True,
            users=_users(manifest, vr, stop,
                         kino=library.kino(Path(commands.main_player_notice_file))),
            panel_surface=self.panel, users_picture=self.users_picture,
            window_verbs=self._apply_the_headsets_verb,
        )
        self.verbs = HeadsetVerbs(
            self.funestra.playback, metadata_root=_metadata_root(manifest),
            vr_dirs=tuple(vr.library_dirs), tilt_deg=remembered.tilt_deg)
        # A VR video nobody has chosen a projection for gets its picture read off
        # the thread that paints it: a fisheye circle found there opens it as a
        # fisheye rather than drawing its lower edge into a point as a 180 does.
        self.verbs.look_with(PictureLook(self.video))

    def _apply_the_headsets_verb(self, command: str) -> bool:
        """The Funestra asks after what runs on it; the verbs are built around
        the playback the Funestra makes, so they are reached through here."""
        return self.verbs.apply(command)

    @property
    def owns_the_slot(self) -> bool:
        """Whether the video is what fills the main slot, Genau's picture
        filling it otherwise."""
        return self.funestra.showing == MainMode.KINO

    @property
    def picture_in_the_slot(self) -> RenderTarget | FrameTexture:
        return self.target if self.owns_the_slot else self.users_picture_texture

    def hangings(self) -> tuple[Hanging, ...]:
        if self.owns_the_slot:
            if not self.target.ready:
                return ()
            return _in_the_slot(self.screen, self.target, _wrap_of(self.verbs, self.target.video))
        if not self.users_picture_texture.ready:
            return ()
        return _in_the_slot(self.screen, self.users_picture_texture,
                            immersive_wrap(self._clip_projection[1]))

    @property
    def wraps_the_viewer(self) -> bool:
        return any(one.wrap is not None for one in self.hangings())

    @property
    def can_dial_the_wrap(self) -> bool:
        wrap = _wrap_of(self.verbs, str(self.funestra.playback.current_video))
        return self.owns_the_slot and wrap is not None and wrap.fov_deg > 0

    @property
    def wrap_readout(self) -> str | None:
        if not self.owns_the_slot:
            return None
        video = str(self.funestra.playback.current_video)
        return readout(self.verbs.projection_of(video), _wrap_of(self.verbs, video))

    def banner_into_the_picture(self):
        """A banner drawn into a picture wrapped round the viewer lands on the seam
        between its two eyes; the floating one carries it instead."""
        if self._notices is None or self.wraps_the_viewer:
            return None
        return self._notices.banner(self.screen_name)

    def flash_the_readout(self, now: float) -> None:
        said = self._readout.frame(self.wrap_readout, now=now)
        if said is not None and self._notices is not None:
            self._notices.flash(said, level=NOTICE, screen=self.screen_name, now=now)

    def dial_the_wrap(self, *, zoom: float, stretch: float) -> None:
        wrap = _wrap_of(self.verbs, str(self.funestra.playback.current_video))
        if zoom != 1.0:
            self.verbs.set_fov(wrap.fov_deg * zoom)
        if stretch != 1.0:
            self.verbs.set_height(wrap.height * stretch)

    def hangs_by(self) -> dict[str, Hangs]:
        return {MAIN: Hangs((self.screen,))}

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def render_latest_frame(self) -> None:
        super().render_latest_frame()
        self._show_the_users_picture()

    def _show_the_users_picture(self) -> None:
        """The frame the User in front put up -- Genau's, scrubbed to the OSR2
        -- uploaded untouched, and wrapped the way its clip is."""
        if self.owns_the_slot:
            return
        picture = self.users_picture.picture
        if picture is None or picture.frame is None:
            return
        if picture.frame is not self._uploaded_frame:
            self.users_picture_texture.upload(picture.frame)
            self._uploaded_frame = picture.frame
            if picture.clip != self._clip_projection[0]:
                self._clip_projection = (
                    picture.clip, default_projection(str(picture.clip), self._vr_dirs))
        if self.users_picture_texture.ready:
            self.screen.rehang_at(
                shown_at(MAIN, self.screen.placement, self.users_picture_texture.aspect),
                self.users_picture_texture.aspect)

    def pump(self, stop: threading.Event, now: float) -> None:
        self._take_presses()
        self.funestra.tick(window=self._window())
        self.verbs.tick(now)
        if self.funestra.stopped:
            stop.set()
        self._watch_progress(now)
        if self._notices is not None:
            self.overlay_banner(self.banner_into_the_picture())

    def _take_presses(self) -> None:
        for event in self._presses.drain():
            if event.screen == PANEL:
                self._press_on_the_panel(event)
            elif event.kind == PRESS:
                self._post(OMNIPAUSE_TOGGLE)
        self._hover_over_the_panel(self._presses.hover, PANEL)

    def _watch_progress(self, now: float) -> None:
        """Say it out loud when the video stops advancing, and reopen it once:
        the failure leaves the player unpaused, its duration known and its
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
        logger.warning("Reopening the main player at %.0fms", position_ms)
        playback.load(playback.index)
        if position_ms:
            playback.seek_to(position_ms)

    def close(self) -> None:
        self.verbs.close()  # before the player: its looks read a picture from one
        self.video.close()  # frees mpv on the thread whose context it renders in
        self.funestra.close()  # what runs on it, and the line to the device
        self.users_picture_texture.close()
        self._close_graphics()


def _users(manifest: LaunchManifest, vr: VrSettings, stop: threading.Event, *, kino):
    """What runs on the Main Funestra: Kino, and Genau where the session names
    its clips folder -- as the desktop's Main Player runs them."""
    users = {MainMode.KINO: kino}
    if vr.clips_folder is None:
        return users
    commands = manifest.commands
    resumed = read_shared_state(shared_state_path(Path(commands.state_dir)))
    users[MainMode.GENAU] = lambda _playback: GenauInTheHeadset(
        clips_folder=vr.clips_folder,
        settings=vr.genau,
        channels=GenauChannels(
            command=Path(commands.genau_cmd_file), paused=Path(commands.genau_paused_file),
            status=Path(commands.genau_status_file), drive=Path(commands.genau_drive_file)),
        notifier=GenauNotifier(vr.notify_host, vr.notify_port),
        tcode_sink=_SaysWhenItFirstMoves(
            UdpTCodeSink(vr.tcode_udp_host, vr.tcode_udp_port), "genau"),
        stop_event=stop,
        start_clip=read_genau_status(Path(commands.genau_status_file)).clip or None,
        latest=False if resumed is None else resumed.genau_latest,
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
        self, player: str, manifest: LaunchManifest, contexts, *,
        vr: VrSettings, remembered: Mapping[str, Placement], notices=None, perf=None,
    ) -> None:
        super().__init__(
            VideoThread(contexts, _headsets_player(contexts), SATELLITE_VIDEO_CAP_PX,
                        name=f"{player}-video", perf=perf),
            remembered.get(player, self.SPOTS[player]),
        )
        commands = manifest.commands
        self.screen_name = player  # its notices flash over its own picture
        self._notices = notices
        # What a launched satellite is given on its command line, read out of
        # the manifest instead: this one runs in the headset's own process.
        channels = SatelliteChannels.from_manifest(
            commands, player,
            play_points=Path(commands.state_dir) / play_points_filename(player))
        self._dashboard_cmd_file = channels.dashboard_cmd
        self._audio_device = vr.audio_device.strip()
        self.panel = PanelBitmap()
        self.funestra = Funestra(
            self.player, channels=channels, playlist=read_playlist(channels.playlist),
            tcode=FunscriptTCodeDriver(_SaysWhenItFirstMoves(
                UdpTCodeSink(vr.tcode_udp_host, vr.tcode_udp_port), player)),
            panel_surface=self.panel,
        )
        self.hud_texture = FrameTexture()
        self.hud_screen = _HangingScreen(self.screen.placement)
        self._hud_version = -1
        self._hud_shown = False
        self._presses = _Presses(player, hud_screen_name(player))

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def hangings(self) -> tuple[Hanging, ...]:
        if not self.target.ready:
            return ()
        picture = Hanging(
            _picture_screen(self.screen_name, self.screen.placement, self.target.aspect),
            mesh=self.screen, picture=self.target)
        if not self.hud_ready:
            return (picture,)
        return (picture, Hanging(
            Screen(hud_screen_name(self.screen_name), self.hud_screen.placement,
                   self.hud_texture.aspect, pressable=True),
            mesh=self.hud_screen, picture=self.hud_texture, blend=True,
            forward_with=self.screen_name))

    def hangs_by(self) -> dict[str, Hangs]:
        return {self.screen_name: Hangs((self.screen,))}

    @property
    def hud_ready(self) -> bool:
        return self._hud_shown and self.hud_texture.ready and self.hud_screen.ready

    def render_latest_frame(self) -> None:
        super().render_latest_frame()
        rgba, version = self.panel.take()
        if version != self._hud_version:
            self._hud_version = version
            self._hud_shown = rgba is not None
            if rgba is not None:
                self.hud_texture.upload(rgba)
        if self._hud_shown and self.target.ready:
            self.hud_screen.placement = attached_to(
                self.funestra.panel_edge, self.shown, aspect=self.target.aspect,
                width_deg=self.hud_texture.width * DEG_PER_PX,
                hanging_aspect=self.hud_texture.aspect, gap_deg=HUD_GAP_DEG,
            )
            self.hud_screen.rehang(self.hud_texture.aspect)

    def pump(self, stop: threading.Event, now: float) -> None:
        self._take_presses()
        self.funestra.tick(window=self._window())
        if self._notices is not None:
            self.overlay_banner(self._notices.banner(self.screen_name))

    def _take_presses(self) -> None:
        for event in self._presses.drain():
            if screen_kind(event.screen) == HUD:
                self._press_on_the_panel(event)
            elif event.kind == PRESS:
                self._post(OMNIPAUSE_TOGGLE)
        self._hover_over_the_panel(self._presses.hover, hud_screen_name(self.screen_name))

    def close(self) -> None:
        self.video.close()  # frees mpv on the thread whose context it renders in
        self.funestra.close()
        self.hud_texture.close()
        self.hud_screen.close()
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


class _PanelUnit:
    """The console the Main Funestra draws, hanging under whichever picture
    fills the main slot -- or, while that picture wraps the viewer and there is
    nothing to dock to, under the dashboard."""

    SPOTS: dict[str, Placement] = {}

    def __init__(self, main_unit: _MainUnit, dash) -> None:
        self._main_unit = main_unit
        self._dash = dash
        self._version = -1
        self.texture = FrameTexture()
        self.screen = _HangingScreen(main_unit.screen.placement)

    def hangings(self) -> tuple[Hanging, ...]:
        if not self.texture.ready:
            return ()
        return (Hanging(
            Screen(PANEL, self.screen.placement, self.texture.aspect, pressable=True),
            mesh=self.screen, picture=self.texture, blend=True, docked_to=MAIN),)

    def hangs_by(self) -> dict[str, Hangs]:
        return {}  # it hangs from whatever is above it, and is never dragged

    def put_back(self) -> None:
        pass  # it follows whatever it is docked under, back to that screen's own spot

    def point(self, frame: Frame) -> None:
        pass  # a squeeze on it is the Main Funestra's, which draws it

    def pump(self, stop: threading.Event, now: float) -> None:
        pass  # painted by the Main Funestra on its own worker

    def render_latest_frame(self) -> None:
        rgba, version = self._main_unit.panel.take()
        if version != self._version:
            self._version = version
            if rgba is not None:
                self.texture.upload(rgba)
        if not self.texture.ready:
            return
        wrapped = self._main_unit.wraps_the_viewer
        slot_aspect = self._main_unit.picture_in_the_slot.aspect
        under, aspect, gap = (
            (self._dash.screen.placement, self._dash.texture.aspect, 0.0) if wrapped else
            (shown_at(MAIN, self._main_unit.screen.placement, slot_aspect), slot_aspect,
             HUD_GAP_DEG))
        self.screen.placement = attached_to(
            panel_hangs_from(self._main_unit.funestra.panel_edge, wrapped=wrapped),
            under, aspect=aspect, width_deg=self.texture.width * DEG_PER_PX,
            hanging_aspect=self.texture.aspect, gap_deg=gap,
        )
        self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()


class _DashUnit:
    # Painted like the console; the pair's handle while it carries one, and a spot for that.

    SPOTS = {
        DASH: Placement(azimuth_deg=0.0, elevation_deg=33.1, width_deg=40.0),
        # Carrying the console, the video having wrapped the viewer: lower, under it.
        PANEL: Placement(azimuth_deg=0.0, elevation_deg=-11.0, width_deg=40.0),
    }

    def __init__(self, main_unit: _MainUnit, *,
                 remembered: Mapping[str, Placement], dashboard_cmd_file: Path,
                 notices: NoticeBoard, dashboard_state_file: Path,
                 reference_flag: Path) -> None:
        self._main_unit = main_unit
        self._notices = notices
        self._state_file = dashboard_state_file
        self._reference_flag = reference_flag
        self._floating = remembered.get(DASH, self.SPOTS[DASH])
        self._wrapped = remembered.get(PANEL, self.SPOTS[PANEL])
        self._pointer = DashPointer(
            post=lambda command: append_command(dashboard_cmd_file, command))
        self._shown_as = preview_marker.shown_as()
        self._presses = _Presses(DASH)
        self._lock = threading.Lock()
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
        return PANEL if self.carrying_the_console else DASH

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
        self._floating, self._wrapped = self.SPOTS[DASH], self.SPOTS[PANEL]
        self.placement = self.SPOTS[self.layout_key]

    def hangings(self) -> tuple[Hanging, ...]:
        if not self.texture.ready:
            return ()
        return (Hanging(
            Screen(DASH, self.screen.placement, self.texture.aspect,
                   movable=True, pressable=True),
            mesh=self.screen, picture=self.texture, blend=True),)

    def hangs_by(self) -> dict[str, Hangs]:
        return {DASH: Hangs((self,), kept_as=lambda: self.layout_key)}

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def pump(self, stop: threading.Event, now: float) -> None:
        size = (DASH_WIDTH_PX, dash_height())
        for event in self._presses.drain():
            if event.kind == PRESS:
                self._pointer.press(*surface_pixel(event.u, event.v, size))
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
        key = (self._pointer.state, records, hover)
        if key == self._key:
            return
        image = paint_dash(self._pointer.state, records, hover, shown_as=self._shown_as)
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

    def __init__(self, dash: _DashUnit, panel: _PanelUnit, *, flag: Path) -> None:
        self._dash = dash
        self._panel = panel
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
            mesh=self.screen, picture=self.texture, blend=True, forward_with=DASH),)

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
        under = self._panel if self._dash.carrying_the_console else self._dash
        self.screen.placement = attached_below(
            under.screen.placement, aspect=under.texture.aspect,
            width_deg=REFERENCE_WIDTH_DEG, hanging_aspect=self.texture.aspect)
        self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()


class _LibraryUnit:
    # A browse to read and press: dead ahead and wide, over the picture while it is up.
    SPOTS = {LIBRARY: Placement(azimuth_deg=0.0, elevation_deg=4.0, width_deg=80.0)}

    def __init__(
        self, *, remembered: Mapping[str, Placement], flag: Path, host,
        main_player_cmd_file: Path, main_player_status_file: Path,
        genau_cmd_file: Path, genau_status_file: Path, genau_has_the_slot: Callable[[], bool],
        dashboard_cmd_file: Path, metadata_root: Path | None,
    ) -> None:
        self._flag = flag
        self._host = host
        self._main_player_cmd_file = main_player_cmd_file
        self._metadata_root = metadata_root
        self._main_player_status_file = main_player_status_file
        self._genau_cmd_file = genau_cmd_file
        self._genau_status_file = genau_status_file
        self._genau_has_the_slot = genau_has_the_slot
        self._browsing_clips = False
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
            mesh=self.screen, picture=self.texture, blend=True, in_front=True),)

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
            if said == PICKED and self._browsing_clips:
                append_command(self._genau_cmd_file, play_file(PlaylistItem(Path(video))))
            elif said == PICKED:
                append_command(self._main_player_cmd_file,
                               play_file(scripted_item(video, self._metadata_root)))
            if said in (PICKED, DISMISSED):
                self._shown.put_away()
                append_command(self._dashboard_cmd_file, BROWSE_LIBRARY_CLOSE)
        if self._shown.asked(read_flag(self._flag, default=False)):
            self._token += 1
            self._opened_at = now
            self._drawn = False
            self._browsing_clips = self._genau_has_the_slot()
            playing = (read_genau_status(self._genau_status_file).clip if self._browsing_clips
                       else read_main_player_status(self._main_player_status_file).video)
            self._host.send(open_line(self._token, playing, clips=self._browsing_clips))
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
BANNER_FONT_PX = 44
BANNER_MAX_WIDTH_PX = 1600
BANNER_PX_PER_DEG = 40.0


class _BannerUnit:
    """The main screen's banner, floating in front of a picture wrapped round the viewer."""

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
            mesh=self.screen, picture=self.texture, blend=True, in_front=True),)

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


class _CoverUnit:  # :mod:`fun_time_vr.cover`, drawn in place of the scene
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
    def wanted(self) -> bool:  # a player with no orchestrator never wants one
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


def set_up_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    install_exception_logging(logger)
    return logger


def main(argv: list[str] | None = None) -> int:
    set_up_logging()
    args = build_parser().parse_args(argv)
    manifest = LaunchManifest.read(args.manifest)
    # The strip that shows this process's notices tails the session's event log,
    # which is appended to per line so several processes can share it.
    _log_into_the_event_log(Path(manifest.commands.dashboard_cmd_file).parent)
    vr = VrSettings.read(args.manifest)
    # Before any window exists: one app, one pinned button (win32_taskbar).
    try:
        set_app_user_model_id(preview_marker.session_identity(preview_marker.shown_as()))
    except OSError:
        logger.debug("Could not claim the taskbar identity", exc_info=True)

    ready = vr_runtime.ensure_ready()
    if ready.readiness is not vr_runtime.Readiness.READY:
        logger.error("VR not available: %s", ready.readiness.value)
        _show_error_popup(vr_runtime.explain(ready))
        return 1
    with ahead_of_background_work():
        return _run(manifest, vr, args.manifest)


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


def _log_into_the_event_log(state_dir: Path) -> None:
    handler = EventLogHandler(event_log_path(state_dir))
    handler.setLevel(NOTICE)  # announcements only; the log file carries the rest
    logging.getLogger("fun_time_vr").addHandler(handler)


def _pump_channels(units: list, stop: threading.Event, perf: FramePerf,
                   hz: float = PUMP_HZ) -> None:
    """The file-channel worker: every unit's flags, drains, status writes and
    repaints — file I/O that can stall under a sync client, so never the frame
    loop's thread.  Two threads on one mpv is its design; see player_core.mpv_gate.
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


def _update_quad_layer(
    session, renderer: SceneRenderer, index: int, unit: _VideoUnit,
    scene_yaw_deg: float, scene_pitch_deg: float,
):
    """Refresh *unit*'s quad swapchain if its texture moved, and describe the
    layer to submit — or None before the first frame of content exists."""
    from .vr_session import QuadLayer  # noqa: PLC0415 (sibling of the lazy VRSession below)

    if unit.layer_dirty:
        session.ensure_quad_swapchain(index, unit.target.width, unit.target.height)
        session.bind_quad_framebuffer(index)
        renderer.copy_texture(unit.target.texture)
        session.release_quad_framebuffer(index)
        unit.layer_dirty = False
        unit.layer_rect = (unit.target.width, unit.target.height)
    if unit.layer_rect is None:
        return None
    position, orientation, size = unit.layer_placement(scene_yaw_deg, scene_pitch_deg)
    return QuadLayer(
        swapchain_index=index, position=position, orientation=orientation, size=size,
    )


def _draw_cover(session, renderer: SceneRenderer, cover: _CoverUnit, views) -> None:
    """Fill both eyes with the cover: ground edge to edge, panel at the heading
    he is facing, latched once he is at the lenses (:class:`CoverAnchor`), through
    the rotation-only view matrix the scene uses so it holds still as he turns."""
    was_held = cover.anchor.held
    heading = cover.anchor.heading(yaw_of_orientation((
        views[0].pose.orientation.x, views[0].pose.orientation.y,
        views[0].pose.orientation.z, views[0].pose.orientation.w,
    )), settled=cover.anchor_settled)
    if was_held is None and cover.anchor.held is not None:
        logger.info("Cover placed at heading %.0f°", math.degrees(heading))
    hanging = yaw_rotation_matrix(heading)
    for eye_index, view in enumerate(views):
        session.bind_eye_framebuffer(eye_index)
        renderer.begin_eye(COVER_CLEAR)
        if cover.screen.ready:
            projection = fov_to_projection_matrix(
                view.fov.angle_left, view.fov.angle_right,
                view.fov.angle_up, view.fov.angle_down,
                0.05, 100.0,
            )
            view_matrix = pose_to_view_matrix(
                (0.0, 0.0, 0.0),
                (view.pose.orientation.x, view.pose.orientation.y,
                 view.pose.orientation.z, view.pose.orientation.w),
            )
            renderer.draw_screen(
                cover.screen.mesh, cover.texture.texture,
                np.ascontiguousarray(projection @ view_matrix @ hanging, dtype=np.float32),
            )
        session.release_eye_framebuffer(eye_index)


class _SaysWhenItFirstMoves:  # places the OSR2's first move against the rest of the log
    def __init__(self, sink, role: str) -> None:
        self._sink = sink
        self._role = role
        self._said = False

    def send(self, command: str) -> None:
        if not self._said:
            self._said = True
            logger.info("First T-Code from the %s role: %s", self._role, command)
        self._sink.send(command)

    def close(self) -> None:
        self._sink.close()


def _scene_is_up(main_unit, satellites: Sequence, panel) -> bool:
    """Every picture the session opens with RENDERED, not merely sized
    (``has_picture``, never ``ready``); the main slot counts once."""
    return bool(
        main_unit.picture_in_the_slot.has_picture
        and panel.texture.has_picture
        and all(satellite.target.has_picture for satellite in satellites)
    )


def _hands_for_the_players(
    library: _LibraryUnit, hands: Mapping[str, HandInput], *, elapsed_s: float,
) -> Mapping[str, HandInput]:
    if not library.takes_the_stick:
        return hands
    library.scroll(scroll_from_stick(strongest(hand.stick_y for hand in hands.values()), elapsed_s))
    return {name: replace(hand, stick_x=0.0, stick_y=0.0) for name, hand in hands.items()}


def _draw_eyes(
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
        view_matrix = pose_to_view_matrix(
            (0.0, 0.0, 0.0),
            (view.pose.orientation.x, view.pose.orientation.y,
             view.pose.orientation.z, view.pose.orientation.w),
        )
        view_proj = projection_matrix @ view_matrix @ scene_rotation
        view_proj32 = np.ascontiguousarray(view_proj, dtype=np.float32)
        if wrap is not None:
            inv32 = np.ascontiguousarray(np.linalg.inv(view_proj), dtype=np.float32)
            renderer.draw_immersive(wrap.wrap, wrap.picture.texture, inv32, eye_index)
        for mesh, texture, blend in flat:
            renderer.draw_screen(mesh, texture, view_proj32, blend=blend)
        pointing.draw(renderer, view_proj32)
        session.release_eye_framebuffer(eye_index)


TEARDOWN_COVER_TIMEOUT_S = 1.0  # a couple of frames; past this, not worth it

FIRST_COVER_TIMEOUT_S = 5.0  # the READY event, a frame or two after the session


def _present_the_cover(session, renderer: SceneRenderer, cover: _CoverUnit) -> bool:
    """One frame of cover; whether it reached the eyes.  Never at the cost of
    the launch or quit it runs inside."""
    try:
        should_render, display_time, views = session.frame_begin()
        cover.render_latest_frame()
        covered = bool(should_render and views and cover.showing)
        if covered:
            _draw_cover(session, renderer, cover, views)
        session.frame_end(display_time, views, project=covered)
        return covered
    except Exception:
        logger.debug("Could not put the cover up", exc_info=True)
        return False


def _raise_the_cover(session, renderer: SceneRenderer, cover: _CoverUnit) -> None:
    """Get the cover in front of the eyes BEFORE the players are built: built
    first and shown after, it was up for the tail of a launch that had already
    finished.  One frame here carries through the mpv bring-up."""
    if not cover.wanted:
        return  # a player with no orchestrator: nothing is coming to wait for
    deadline = time.monotonic() + FIRST_COVER_TIMEOUT_S
    while session.running and time.monotonic() < deadline:
        session.poll_events()
        if session.session_ready and _present_the_cover(session, renderer, cover):
            return
        time.sleep(0.005)


# Past this no session is coming (docs/entering-vr.md).
HEADSET_HOLD_TIMEOUT_S = 180.0


def _hold_the_headset(
    session, renderer: SceneRenderer, cover: _CoverUnit, state_dir: Path,
) -> None:
    """Cover the headset until the next session releases it."""
    report_the_headset_held(state_dir)
    deadline = time.monotonic() + HEADSET_HOLD_TIMEOUT_S
    try:
        while session.running and time.monotonic() < deadline:
            if not headset_hold_asked(state_dir):
                return
            session.poll_events()
            if not session.session_ready:
                return  # nothing is being presented; there is nothing to hold
            cover.refresh()
            _present_the_cover(session, renderer, cover)
    except Exception:
        logger.debug("Could not hold the headset's cover", exc_info=True)


def _cover_the_teardown(session, renderer: SceneRenderer, cover: _CoverUnit) -> None:
    """Raise the closing cover and hold it while this process comes apart.  The
    ordinary quit asks through the shutdown progress file; this is the other way
    out — window closed, interrupt, a runtime that ended the session."""
    cover.closing_now()
    try:
        cover.refresh()
        deadline = time.monotonic() + TEARDOWN_COVER_TIMEOUT_S
        while session.running and time.monotonic() < deadline:
            session.poll_events()
            if not session.session_ready:
                break  # nothing is being presented; there is nothing to cover
            if _present_the_cover(session, renderer, cover):
                break
    except Exception:
        # A compositor that has let go of us must not turn a quit into a crash.
        logger.debug("Could not raise the closing cover", exc_info=True)
    cover.settled()  # however that went, teardown has waited long enough


def _run(manifest: LaunchManifest, vr: VrSettings, manifest_path: Path) -> int:
    # The GL/XR stack loads only after the runtime probe found a headset.
    import glfw  # noqa: PLC0415
    import xr  # noqa: PLC0415

    from .vr_session import VRSession  # noqa: PLC0415

    opened = open_vr_session(
        VRSession, device_not_ready=xr.exception.GraphicsDeviceInvalidError)
    if opened.session is None:
        _show_error_popup(opened.message)
        return 1
    session = opened.session

    renderer = SceneRenderer()
    contexts = session.shared_contexts()
    perf = FramePerf(logger=logger)
    stop = threading.Event()
    commands = manifest.commands
    state_dir = Path(commands.dashboard_cmd_file).parent
    layout_path = state_dir / LAYOUT_FILENAME
    migrate_layout(layout_path)
    remembered = read_layout(layout_path)
    # Before the players and refreshed between them: each opens media.
    cover = _CoverUnit(state_dir)
    _raise_the_cover(session, renderer, cover)
    # One read of the event log per tick, pumped before every screen's banner.
    notices = NoticeBoard(event_log_path(state_dir), unlogged=UnloggedNotices(state_dir))
    main_unit = _MainUnit(
        manifest, vr, contexts, remembered=remembered, stop=stop, notices=notices, perf=perf,
        while_reading=lambda: _present_the_cover(session, renderer, cover))
    _present_the_cover(session, renderer, cover)
    satellites = [
        _SatelliteUnit(player, manifest, contexts, vr=vr, remembered=remembered.placements,
                       notices=notices, perf=perf)
        for player in (PORTRAIT, LANDSCAPE)
    ]
    _present_the_cover(session, renderer, cover)
    reference_flag = Path(state_dir) / REFERENCE_OPEN_FILENAME
    dash = _DashUnit(
        main_unit,
        remembered=remembered.placements,
        dashboard_cmd_file=Path(commands.dashboard_cmd_file),
        notices=notices,
        dashboard_state_file=Path(commands.dashboard_state_file),
        reference_flag=reference_flag,
    )
    panel = _PanelUnit(main_unit, dash)
    reference = _ReferenceUnit(dash, panel, flag=reference_flag)
    library = _LibraryUnit(
        remembered=remembered.placements,
        flag=Path(state_dir) / LIBRARY_OPEN_FILENAME,
        host=LibraryHost(manifest_path=manifest_path, state_dir=Path(state_dir)),
        main_player_cmd_file=Path(commands.main_player_cmd_file),
        main_player_status_file=Path(commands.main_player_status_file),
        genau_cmd_file=Path(commands.genau_cmd_file),
        genau_status_file=Path(commands.genau_status_file),
        genau_has_the_slot=lambda: not main_unit.owns_the_slot,
        dashboard_cmd_file=Path(commands.dashboard_cmd_file),
        metadata_root=_metadata_root(manifest),
    )
    banner = _BannerUnit(main_unit, notices)
    keeper = _LayoutKeeper(layout_path, remembered)
    scene_ready = SceneReady(scene_ready_file(state_dir))
    cover_seen = CoverSeen()
    posts = _ControllerPosts(Path(commands.dashboard_cmd_file))
    wear = HeadsetWear()
    # The room, each thing saying for itself what it hangs there.  Dash before
    # panel: the console hangs off where the dashboard ended up.
    units = [main_unit, *satellites, dash, panel, reference, library, banner, cover]
    closing = [notices, *units, keeper, posts]
    # The Main Funestra ticks on a worker of its own, at Genau's rate.
    pumped = [one for one in closing if one is not main_unit]
    where = room.where_they_hang(units)
    pointer = Pointer()
    thumbs = Thumbs()
    stacking = Stacking()
    pointing = _PointerDrawing()
    use_layers = vr.compositor_layers
    # The recentering yaw, with the role's tilt read in beside it each frame.
    scene_yaw = 0.0
    scene_rotation = np.eye(4, dtype=np.float32)
    last_frame_time = time.monotonic()
    cover_up_at: float | None = None
    cover_down_said = False
    pump_thread = start_daemon_thread(
        target=_pump_channels, args=(pumped, stop, perf), name="file-channels",
    )
    main_thread = start_daemon_thread(
        target=_pump_channels, args=([main_unit], stop, perf), kwargs={"hz": MAIN_TICK_HZ},
        name="main-funestra",
    )
    logger.info(
        "Entering the VR loop (three Funestras up, compositor layers %s)",
        "on" if use_layers else "off",
    )

    try:
        while session.running and not stop.is_set():
            session.poll_events()
            posts.post(wear.said(session.focused, now=time.monotonic()))
            if session.window_close_requested():
                break
            if cover.holding:
                break  # the session is crossing over; the teardown holds it up

            if not session.session_ready:
                # The pump thread keeps every channel live while the headset
                # warms up, so the orchestrator sees status the moment it asks.
                cover.settled()  # nothing can be shown from here, cover included
                glfw.poll_events()
                time.sleep(0.01)
                continue

            now = time.monotonic()
            frame_dt = now - last_frame_time
            last_frame_time = now

            t0 = time.perf_counter()
            should_render, display_time, views = session.frame_begin()
            t1 = time.perf_counter()
            for unit in units:
                unit.render_latest_frame()
            # The only place that sees the room fill in, and whether anyone
            # had the headset on while it did.
            room_is_up = _scene_is_up(main_unit, satellites, panel)
            cover.anchor_settled = cover_seen.dwelt and session.views_tracked
            cover.awaiting_wearer = room_is_up and not session.focused  # set before he looks
            scene_ready.note(room_is_up and cover_seen.dwelt)
            t2 = time.perf_counter()

            quads = []
            project = False
            covered = False
            t3 = t2
            if should_render and views and cover.showing:
                # No quads: the runtime composites those OVER our layer, so a
                # screen submitted as one shows through the cover.
                covered = True
                if cover_up_at is None:
                    cover_up_at = now
                    logger.info("Cover drawing to the headset")
                project = True
                t3 = time.perf_counter()
                _draw_cover(session, renderer, cover, views)
            elif should_render and views:
                if session.focused:
                    for unit in (main_unit, *satellites):
                        unit.route_audio()
                if main_unit.verbs.recenter.take():
                    scene_yaw = yaw_of_orientation((
                        views[0].pose.orientation.x, views[0].pose.orientation.y,
                        views[0].pose.orientation.z, views[0].pose.orientation.w,
                    ))
                    logger.info(
                        "Recentered the scene onto heading %.0f°", math.degrees(scene_yaw)
                    )
                reset = main_unit.verbs.layout_reset.take()
                if reset:
                    pointer.let_go()
                    logger.info(
                        "Put the players and the dashboard back in their default spots and sizes")
                session.sync_controller(display_time)
                scene_rotation = _scene_rotation(scene_yaw, main_unit.verbs.tilt_deg)
                hangings = room.what_hangs(units)
                screens = room.arranged(stacking, hangings)
                head = head_position([
                    (view.pose.position.x, view.pose.position.y, view.pose.position.z)
                    for view in views
                ])
                frame = pointer.frame(
                    session.hands, head=head, scene_rotation=scene_rotation, screens=screens)
                if frame.taken is not None:
                    stacking.take(frame.taken)
                dialing = main_unit.can_dial_the_wrap
                thumb = thumbs.frame(
                    _hands_for_the_players(library, session.hands, elapsed_s=frame_dt),
                    pointer, elapsed_s=frame_dt, dialing=dialing)
                posts.post(thumb.commands)
                zoom = main_unit.verbs.angle_asked.take()
                if dialing:
                    main_unit.dial_the_wrap(zoom=zoom, stretch=thumb.stretch)
                main_unit.flash_the_readout(now)
                scene_yaw, lift_deg = carried_heading(scene_yaw, frame.carried)
                main_unit.verbs.nudge_tilt(lift_deg)
                scene_pitch_deg = main_unit.verbs.tilt_deg
                scene_rotation = _scene_rotation(scene_yaw, scene_pitch_deg)
                players = {name: frame.moved.get(name, where[name].placement)
                           for name in PLAYERS}
                moved = frame.moved | rearranged(
                    players, grow=thumb.grow, nearer_by=thumb.nearer)
                for name, placement in moved.items():
                    where[name].put(placement)  # its own spot, of however many
                    keeper.place(where[name].spot(name), placement)
                keeper.tilt(scene_pitch_deg, carried=bool(lift_deg))
                if reset:
                    for unit in units:
                        unit.put_back()
                    keeper.forget()
                if frame.settled or thumb.settled or reset:
                    keeper.settle()
                for unit in units:
                    unit.point(frame)
                pointing.update(frame, screens, held_controllers(
                    session.hands, head=head, scene_rotation=scene_rotation))
                as_quads: set[str] = set()
                if use_layers:
                    # The mpv-backed screens as quads, out of the scene as each is
                    # taken -- and a picture wrapping the view is never taken.
                    for index, unit in enumerate([main_unit, *satellites]):
                        if unit is main_unit and (
                                main_unit.wraps_the_viewer or not main_unit.owns_the_slot):
                            continue
                        quad = _update_quad_layer(
                            session, renderer, index, unit,
                            math.degrees(scene_yaw), scene_pitch_deg,
                        )
                        if quad is not None:
                            quads.append(quad)
                            as_quads.add(unit.screen_name)
                project = True  # the panel lives in the projection layer
                t3 = time.perf_counter()
                _draw_eyes(session, renderer, hangings, pointing, views, scene_rotation,
                           screens=screens, as_quads=as_quads)
            t4 = time.perf_counter()
            session.frame_end(display_time, views, project=project, quads=quads)
            t5 = time.perf_counter()
            cover_seen.note(covered and session.focused)
            if cover_up_at is not None and not covered and not cover_down_said:
                cover_down_said = True
                logger.info("Cover down after %.1fs on screen", now - cover_up_at)
            if covered or not should_render:
                cover.settled()
            glfw.poll_events()
            perf.note("wait", (t1 - t0) * 1e3)
            perf.note("pictures", (t2 - t1) * 1e3)
            perf.note("layers", (t3 - t2) * 1e3)
            perf.note("eyes", (t4 - t3) * 1e3)
            perf.note("end", (t5 - t4) * 1e3)
            perf.frame_done()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        _cover_the_teardown(session, renderer, cover)
        stop.set()
        # Only to settle the file channels — player_core.mpv_gate makes the closes safe.
        pump_thread.join(timeout=2.0)
        main_thread.join(timeout=2.0)
        held = headset_hold_asked(state_dir)
        stop_runtime = held and headset_hold_stops_the_runtime(state_dir)  # while it is there
        _close_channels(closing, keep=cover if held else None)
        if held:
            _hold_the_headset(session, renderer, cover, state_dir)
            cover.close()
        pointing.close()
        renderer.close()
        session.close()
        logger.info("Shutdown complete")
        if stop_runtime:  # the orchestrator that knew this exited under the hold
            vr_runtime.stop_the_runtime_a_session_started(state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
