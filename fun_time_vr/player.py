"""The VR player process: the session's four players composited into one
OpenXR scene.

The desktop session runs Nau, Genau and two satellite processes, each owning
a window; an OpenXR runtime gives the headset to a single rendering process,
so in VR all four are surfaces of this one — :class:`fun_time_vr.roles.MainRole`,
:class:`fun_time_vr.genau_role.GenauRole` on a thread of its own, and the
satellite package's own session against offscreen players.  Each keeps its
desktop sibling's whole contract — the playlist/command/paused/status file
quartet — so the orchestrator, dispatch loop, voice control and device arbiter
drive them without knowing the display changed.  The console hangs in the scene
as a panel of its own (:mod:`fun_time_vr.console_panel`), and the controllers
point at it and at the satellites (:mod:`fun_time_vr.pointer`).

Two threads: ``_pump_channels`` owns every file channel at its own cadence,
because file I/O under a sync client can stall for arbitrary milliseconds and
none of it may ride the frame loop.  The render thread owns GL: it waits on the
compositor, lets each mpv render its latest frame into that unit's texture when
one is newly due (the videos' 24-30fps never paces the 90Hz loop), and hands
the compositor its layers.

With ``vr.compositor_layers=true``, flat screens are submitted as compositor
quad layers instead of drawn in-scene; what that costs, and why it is off by
default, is in docs/known-issues.md.  Both ends of a session are covered from
here too (:mod:`fun_time_vr.cover`), the headset having no monitors for the
desktop's overlay windows to sit on.

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
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from app_support import ports
from app_support.threading_utils import start_daemon_thread
from app_support.win32 import set_app_user_model_id
from player_core.drive_gate import DriveGate
from player_core.file_channel import append_command, consume_command_file, read_paused_state
from player_core.genau_notifier import GenauNotifier
from player_core.playlist import read_playlist
from player_core.render_player import MpvRenderPlayer
from player_core.status import StatusWriter
from player_core.tcode import UdpTCodeSink
from player_core.tcode_driver import FunscriptTCodeDriver
from player_core.timeline import TIMELINE_HEIGHT, progress_bar_bgra
from player_core.volume import VolumeHud, VolumeHudPainter, chip_xy

from fun_time.manifest import LaunchManifest
from fun_time.player_status import genau_status_path, read_genau_status
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.win32_taskbar import VR_APP_USER_MODEL_ID
from satellite.hud_overlay import HudOverlay
from satellite.runtime import apply_command as apply_satellite_command
from satellite.session import SatelliteSession
from satellite.status import status_fields as satellite_status_fields

from . import vr_runtime
from .console_panel import PANEL_WIDTH_PX, PanelPointer, paint_panel, panel_hud, panel_painter
from .cover import (
    COVER_CLEAR,
    COVER_WIDTH_DEG,
    Cover,
    CoverWatcher,
    SceneReady,
    paint_cover,
    scene_ready_file,
)
from .furniture import chip_state, scrubber_state
from .genau_role import GenauRole, run_ticks
from .genau_settings import GenauSettings
from .layout import LANDSCAPE, LAYOUT_FILENAME, PANEL, PORTRAIT, read_layout, write_layout
from .matrices import (
    fov_to_projection_matrix,
    pitch_rotation_matrix,
    pose_to_view_matrix,
    yaw_of_orientation,
    yaw_rotation_matrix,
)
from .perf import FramePerf
from .pointer import (
    DRAG,
    PRESS,
    RELEASE,
    SURFACE,
    Frame,
    Pointer,
    PressEvent,
    Screen,
    cursor_vertices,
    handle_vertices,
    head_position,
    laser_vertices,
)
from .render import FrameTexture, RenderTarget, SceneRenderer, ScreenMesh, immersive_mode
from .roles import UNIMPLEMENTED_NAU_VERBS, MainRole
from .satellite_hud import (
    HUD,
    HUD_DEG_PER_PX,
    HUD_GAP_DEG,
    PICTURE,
    HudSurface,
    SatellitePointer,
    hud_screen_name,
    screen_kind,
)
from .scene import (
    PRIMARY_PLACEMENT,
    Placement,
    attached_below,
    quad_layer_placement,
    surface_vertices,
)

logger = logging.getLogger(__name__)

# Overlay ids shared with the desktop satellite (10 is its lock HUD).
_OV_SCRUBBER = 11
_OV_VOLUME = 12

# Longest texture side each video gets: near-native for the primary, and for
# a satellite's 28° of view well above what the headset resolves there.
PRIMARY_VIDEO_CAP_PX = 4096
SATELLITE_VIDEO_CAP_PX = 2048

# GenauVR's rate and deadzone, but not its sign: our stick away lowers.
TILT_RATE_DEG_S = 85.0
CONTROLLER_DEADZONE = 0.1

# The file-channel worker's cadence: the dispatch loop polls these same files
# at ~20Hz, so 30Hz loses no responsiveness.
PUMP_HZ = 30.0

# How long session bring-up tolerates a cold-started runtime whose graphics
# device is still coming up (see _run's retry loop), and how often it retries.
# Well inside the orchestrator's 120s first-status timeout.
SESSION_BRINGUP_TIMEOUT_S = 60.0
SESSION_BRINGUP_RETRY_S = 2.0

_MUTED_INDICATOR = VolumeHud(volume=0, muted=True)

LASER_REACH_M = 3.0  # when the laser meets no screen
HANDLE_COLOR = (0.85, 0.85, 0.9, 0.35)
HOT_HANDLE_COLOR = (0.3, 0.6, 1.0, 0.85)
LASER_COLOR = (1.0, 1.0, 1.0, 0.55)
CURSOR_COLOR = (1.0, 1.0, 1.0, 0.9)


def _show_error_popup(message: str) -> None:
    """Say why the headset never lit up -- a hidden launch that just exits
    is indistinguishable from a crash."""
    from shared_ui.alert import show_alert

    show_alert("FunTimeVR", message, icon=PROJECT_VR_ICON)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FunTimeVR player (one OpenXR scene, three players)")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="The windows bridge manifest INI the orchestrator wrote")
    return parser


def _folders(spec: str) -> tuple[Path, ...]:
    return tuple(Path(part) for part in spec.split("|") if part.strip())


@dataclass(frozen=True)
class VrSettings:
    """The ``[vr]`` section the VR orchestrator adds to the launch manifest.

    FunTimeVR's own half of the schema, read here rather than in
    :mod:`fun_time.manifest` because a desktop session never writes it.
    """

    tcode_udp_host: str
    tcode_udp_port: int
    library_dirs: tuple[Path, ...]
    audio_device: str
    compositor_layers: bool
    # Genau's role: its folder, its companion's address, its engine's numbers.
    clips_dirs: tuple[Path, ...] = ()
    vr_clip_dirs: tuple[Path, ...] = ()
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
            clips_dirs=_folders(vr.get("clips_dirs", "")),
            vr_clip_dirs=_folders(vr.get("vr_clip_dirs", "")),
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
        hanging = (self.placement, aspect)
        if hanging == self._hanging:
            return
        if self.mesh is None:
            self.mesh = ScreenMesh()
        self.mesh.upload(surface_vertices(self.placement, aspect=aspect))
        self._hanging = hanging

    def close(self) -> None:
        if self.mesh is not None:
            self.mesh.close()


class _VideoUnit:
    """What every mpv-backed player shares: an offscreen mpv and a texture target."""

    def __init__(self, player, target_cap_px: int, placement: Placement = PRIMARY_PLACEMENT) -> None:
        self.player = player
        self.target = RenderTarget()
        self.screen = _HangingScreen(placement)
        self._target_cap_px = target_cap_px
        # Compositor-layer bookkeeping, render-thread-owned: whether the
        # target holds pixels its quad swapchain hasn't copied yet, and the
        # size the swapchain's content was copied at (None until the first
        # copy, and again after a resize makes the copied image stale).
        self.layer_dirty = False
        self.layer_rect: tuple[int, int] | None = None
        # Furniture last painted, pump-thread-owned.
        self._scrubber_shown: tuple | None = None
        self._chip_shown: tuple | None = None

    def render_latest_frame(self) -> None:
        width, height = self.player.video_dims
        if width and height:
            scale = min(1.0, self._target_cap_px / max(width, height))
            sized = (max(1, round(width * scale)), max(1, round(height * scale)))
            if sized != (self.target.width, self.target.height):
                self.target.ensure(*sized)
                self.layer_rect = None
        if self.target.ready:
            self.screen.rehang(self.target.aspect)
        if self.target.ready and self.player.has_new_frame:
            # flip_y: mpv renders top-left-origin; the scene samples GL
            # lower-left convention (verified against a top-half-white clip).
            self.player.render(self.target.fbo, self.target.width, self.target.height, flip_y=True)
            self.layer_dirty = True

    def layer_placement(self, scene_yaw_deg: float = 0.0, scene_pitch_deg: float = 0.0):
        """Pose and size for this screen's compositor quad, at the aspect its
        swapchain last copied; the scene angles turn and tilt the arrangement."""
        width, height = self.layer_rect
        return quad_layer_placement(
            self.screen.placement, aspect=width / height,
            scene_yaw_deg=scene_yaw_deg, scene_pitch_deg=scene_pitch_deg,
        )

    def overlay_furniture(self, position_ms: float, duration_ms: float, volume_hud, painter) -> None:
        """The scrubber along the lower edge and the volume chip at its right end,
        exactly the furniture the desktop players draw — repainted only when
        what they show moves (see :mod:`fun_time_vr.furniture`)."""
        if not self.target.ready:
            return
        width, height = self.target.width, self.target.height
        scrubber = scrubber_state(width, height, position_ms, duration_ms)
        if scrubber != self._scrubber_shown:
            self._scrubber_shown = scrubber
            bar = progress_bar_bgra(position_ms, duration_ms, None, width)
            self.player.overlay(_OV_SCRUBBER, 0, height - bar.shape[0], bar)
        chip = chip_state(width, height, volume_hud)
        if chip != self._chip_shown:
            self._chip_shown = chip
            x, y = chip_xy(win_w=width, win_h=height, timeline_h=TIMELINE_HEIGHT)
            self.player.overlay(_OV_VOLUME, x, y, painter.bgra(volume_hud))

    def pump(self, stop: threading.Event, now: float) -> None:
        """One turn of the file-channel worker — what every unit owes it."""
        raise NotImplementedError

    def close(self) -> None:  # what the frame loop's `finally` calls
        raise NotImplementedError

    def _close_graphics(self) -> None:
        self.target.close()
        self.screen.close()


class _MainUnit(_VideoUnit):
    def __init__(self, manifest: LaunchManifest, vr: VrSettings, get_proc_address) -> None:
        # Muted at birth: the headset's sink cannot be trusted until the
        # compositor is presenting (see route_audio).
        super().__init__(
            MpvRenderPlayer(get_proc_address, muted=True, loop_file=True),
            PRIMARY_VIDEO_CAP_PX,
        )
        commands = manifest.commands
        self.cmd_file = Path(commands.nau_cmd_file)
        self.paused_file = Path(commands.nau_paused_file)
        metadata_raw = manifest.regen.metadata_root.strip()
        driver = FunscriptTCodeDriver(
            UdpTCodeSink(vr.tcode_udp_host, vr.tcode_udp_port)
        )
        self.role = MainRole(
            player=self.player,
            driver=driver,
            playlist_file=Path(commands.nau_playlist_file),
            metadata_root=Path(metadata_raw) if metadata_raw else None,
            vr_dirs=tuple(
                vr.library_dirs
            ),
            start_paused=read_paused_state(self.paused_file, logger=logger),
        )
        # The panel's forecasts of Genau's publish, and the touch each status carries.
        self.drive_gate = DriveGate(self.role)
        self._audio_device = vr.audio_device.strip()
        self._audio_routed = False
        self._status_writer = StatusWriter(
            Path(commands.nau_status_file),
            lambda role: role.status_fields(self.drive_gate.handoff_touch()),
        )
        self._volume_painter = VolumeHudPainter()
        self._unhandled: set[str] = set()

    def route_audio(self) -> None:
        """Give the primary its sound on the first frame the headset is WORN.

        Routed earlier — at construction, or on VISIBLE with the headset on its
        stand — the parked endpoint takes the stream without consuming it, and
        mpv's audio clock (which the video clock follows) never ticks: the
        primary frozen on frame 1 for the session.  FOCUSED means a human is
        wearing it, endpoints draining.
        """
        if self._audio_routed:
            return
        self._audio_routed = True
        if self._audio_device:
            picked = self.player.set_audio_device_matching(self._audio_device)
            logger.info(
                "Audio device %r -> %s", self._audio_device, picked or "no match; default"
            )
        # Hand the level back to the role, so whatever the session set while
        # the headset warmed up (a SET_VOLUME, a mute) is what comes on.
        self.role.audio_live = True
        self.player.set_volume(self.role.volume)
        self.player.set_muted(self.role.muted)

    def pump(self, stop: threading.Event, now: float) -> None:
        self.role.set_paused(read_paused_state(self.paused_file, logger=logger))
        for line in consume_command_file(self.cmd_file, logger=logger, uppercase=False):
            if not self.role.apply_command(line, on_quit=stop.set):
                keyword = line.split(None, 1)[0].upper() if line.split() else line
                if keyword not in self._unhandled:
                    self._unhandled.add(keyword)
                    logger.info(
                        "Verb the VR main role does not handle: %s (%s)", keyword,
                        UNIMPLEMENTED_NAU_VERBS.get(keyword, "not a verb it knows at all"),
                    )
        self.role.tick(now)
        self._status_writer.write(self.role)
        self.overlay_furniture(
            self.role.position_ms, self.role.duration_ms,
            VolumeHud(volume=self.role.volume, muted=self.role.muted), self._volume_painter,
        )

    def close(self) -> None:
        self.role.close()  # closes driver + player
        self._close_graphics()


class _SatelliteUnit(_VideoUnit):
    def __init__(
        self, side: str, manifest: LaunchManifest, get_proc_address, *, placement: Placement,
    ) -> None:
        # audio=False, not merely muted: any audio chain here can wedge on the
        # headset's parked endpoint and freeze the video clock (see route_audio).
        super().__init__(
            MpvRenderPlayer(
                get_proc_address, muted=True, loop_file=False, prefetch=True, audio=False,
            ),
            SATELLITE_VIDEO_CAP_PX,
            placement,
        )
        commands = manifest.commands
        self.side = side
        self.cmd_file = Path(commands.side_file(side, "cmd"))
        self.paused_file = Path(commands.side_file(side, "paused"))
        self.playlist_file = Path(commands.side_file(side, "playlist"))
        self.session = SatelliteSession(
            self._read_playlist(),
            player=self.player,
            start_paused=read_paused_state(self.paused_file, logger=logger),
        )
        self._status_writer = StatusWriter(
            Path(commands.side_file(side, "status")), satellite_status_fields
        )
        self.hud_surface = HudSurface()
        self.hud = HudOverlay(
            hud_file=Path(commands.side_file(side, "hud")),
            command_file=Path(commands.dashboard_cmd_file),
            player=self.hud_surface,
        )
        self.hud_texture = FrameTexture()
        self.hud_screen = _HangingScreen(placement)
        self._hud_version = -1
        self._hud_shown = False
        self._presses = _Presses(side, hud_screen_name(side))
        self._pointer = SatellitePointer(
            hud=self.hud, seek=self.session.seek_to, duration_ms=lambda: self.session.duration_ms,
        )
        self._volume_painter = VolumeHudPainter()

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    @property
    def hud_ready(self) -> bool:
        return self._hud_shown and self.hud_texture.ready and self.hud_screen.ready

    def render_latest_frame(self) -> None:
        super().render_latest_frame()
        rgba, version = self.hud_surface.take()
        if version != self._hud_version:
            self._hud_version = version
            self._hud_shown = rgba is not None
            if rgba is not None:
                self.hud_texture.upload(rgba)
        if self._hud_shown and self.target.ready:
            self.hud_screen.placement = attached_below(
                self.screen.placement, aspect=self.target.aspect,
                width_deg=self.hud_texture.width * HUD_DEG_PER_PX,
                hanging_aspect=self.hud_texture.aspect, gap_deg=HUD_GAP_DEG,
            )
            self.hud_screen.rehang(self.hud_texture.aspect)

    def _surface_size(self, kind: str) -> tuple[int, int]:
        if kind == HUD:
            return self.hud_surface.size or (1, 1)
        return max(1, self.target.width), max(1, self.target.height)

    def _read_playlist(self) -> list[Path]:
        return [video for video, _funscript in read_playlist(self.playlist_file)]

    def _reload_playlist(self) -> None:
        reloaded = self._read_playlist()
        if reloaded:
            self.session.replace_playlist(reloaded)

    def pump(self, stop: threading.Event, now: float) -> None:
        self.session.set_paused(read_paused_state(self.paused_file, logger=logger))
        for command in consume_command_file(self.cmd_file, logger=logger, uppercase=False):
            apply_satellite_command(
                command, self.session, stop_event=None, reload_playlist=self._reload_playlist,
            )
        self.session.advance()
        self._status_writer.write(self.session)
        self.hud.tick(video=self.session.current_video.stem)
        for event in self._presses.drain():
            if event.kind == PRESS:
                kind = screen_kind(event.screen)
                self._pointer.press(kind, event.u, event.v, size=self._surface_size(kind))
        hover = self._presses.hover
        kind = screen_kind(hover[0]) if hover is not None else PICTURE
        self._pointer.hover(
            kind, hover[1] if hover is not None else None, size=self._surface_size(kind))
        self.overlay_furniture(
            self.session.position_ms, self.session.duration_ms,
            _MUTED_INDICATOR, self._volume_painter,
        )

    def close(self) -> None:
        self.session.close()  # closes the player
        self.hud_texture.close()
        self.hud_screen.close()
        self._close_graphics()


class _GenauUnit:
    """Genau's surface: the frame its engine chose, on the primary's screen or
    wrapped round the viewer by the clip's projection.  No mpv under it and
    no furniture on it; the engine ticks on a thread of its own."""

    def __init__(self, manifest: LaunchManifest, vr: VrSettings, stop: threading.Event) -> None:
        if not vr.clips_dirs:
            raise RuntimeError("the launch manifest names no clips folder for Genau's role")
        commands = manifest.commands
        genau_state = Path(commands.genau_cmd_file).parent
        self.role = GenauRole(
            clips_dirs=vr.clips_dirs,
            vr_dirs=vr.vr_clip_dirs,
            settings=vr.genau,
            command_file=Path(commands.genau_cmd_file),
            paused_file=Path(commands.genau_paused_file),
            drive_file=genau_state / "genau_drive.txt",
            console_file=Path(commands.nau_console_file),
            notifier=GenauNotifier(vr.notify_host, vr.notify_port),
            tcode_sink=UdpTCodeSink(vr.tcode_udp_host, vr.tcode_udp_port),
            stop_event=stop,
            # Genau's own resume: the clip it was left showing, off its last status.
            start_clip=read_genau_status(genau_status_path(genau_state)).clip or None,
        )
        self.texture = FrameTexture()
        self.screen = _HangingScreen(PRIMARY_PLACEMENT)

    def render_latest_frame(self) -> None:
        frame = self.role.take_frame()
        if frame is None:
            return
        self.texture.upload(frame)
        self.screen.rehang(self.texture.aspect)

    def pump(self, stop: threading.Event, now: float) -> None:
        """Nothing: the engine turns its channels on its own thread."""

    def close(self) -> None:
        self.role.close()
        self.texture.close()
        self.screen.close()


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


class _PanelUnit:
    """The console, hanging in the scene: painted and pressed on the pump thread,
    uploaded on the render thread when it changed."""

    def __init__(
        self, primary: _MainUnit, genau: _GenauUnit, *, placement: Placement,
        dashboard_cmd_file: Path,
    ) -> None:
        self._primary = primary
        self._genau = genau
        self._painter = panel_painter()
        self._chip_painter = VolumeHudPainter()
        self._pointer = PanelPointer(
            self._painter,
            post=lambda command: append_command(dashboard_cmd_file, command),
            seek=primary.role.seek_to,
        )
        self._presses = _Presses(PANEL)
        self._lock = threading.Lock()
        self._image = None
        self._key = None
        self._width = PANEL_WIDTH_PX
        self._uploaded = None
        self.texture = FrameTexture()
        self.screen = _HangingScreen(placement)

    def point(self, frame: Frame) -> None:
        self._presses.point(frame)

    def _take_presses(self) -> None:
        for event in self._presses.drain():
            if event.kind == PRESS:
                self._pointer.press(event.u, event.v)
            elif event.kind == DRAG:
                self._pointer.drag(event.u, event.v)
            elif event.kind == RELEASE:
                self._pointer.release()

    def pump(self, stop: threading.Event, now: float) -> None:
        self._take_presses()
        genau, main = self._genau.role, self._primary.role
        clip = genau.current_clip
        hud = panel_hud(
            genau.console_hud,
            video_title=main.current_video.stem,
            clip_title=clip.stem if clip is not None else "",
            loading=genau.loading,
            drive_gate=self._primary.drive_gate,
            f_mode=main.f_mode,
            playback_speed=main.speed,
        )
        if genau.showing:
            scrubber = None
            chip = VolumeHud(volume=genau.volume, muted=genau.muted)
        else:
            scrubber = (main.position_ms, main.duration_ms)
            chip = VolumeHud(volume=main.volume, muted=main.muted)
        hovered = self._presses.hover
        hover = self._pointer.tooltip_anchor(hovered[1] if hovered is not None else None)
        # Repainted only when what it shows moves, as the furniture is.
        key = (
            hud,
            scrubber_state(self._width, 1, *scrubber) if scrubber is not None else None,
            chip,
            hover,
        )
        if key == self._key:
            return
        image = paint_panel(
            self._painter, hud, scrubber=scrubber, chip=chip, chip_painter=self._chip_painter,
            hover=hover,
        )
        self._pointer.painted(image.size, scrubber=scrubber, chip=chip)
        with self._lock:
            self._image = image
        self._key = key
        self._width = image.width

    def render_latest_frame(self) -> None:
        with self._lock:
            image = self._image
        if image is None or image is self._uploaded:
            return
        self.texture.upload(np.asarray(image))
        self._uploaded = image
        self.screen.rehang(self.texture.aspect)

    def close(self) -> None:
        self.texture.close()
        self.screen.close()


class _CoverUnit:
    """The covers (:mod:`fun_time_vr.cover`), hanging dead ahead: read and
    painted on the pump thread, uploaded and drawn on the render thread, in
    place of the scene."""

    def __init__(self, state_dir: Path) -> None:
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
        self.refresh()  # the loop's first renderable frame is already covered

    def closing_now(self) -> None:
        """Ending on its own, so raise the closing cover unasked."""
        self._watcher.closing_now()

    @property
    def closing(self) -> bool:
        """Whether the painted cover is the teardown's.  Off the bitmap, not the
        texture, so a teardown holding its first kill is answered even where no
        frame was drawn."""
        with self._lock:
            return self._pending is not None and self._pending[1]

    def refresh(self) -> None:
        """Re-read the progress files and repaint if what they say has moved."""
        cover = self._watcher.read()
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
        headset off would else buy the timeout and no cover."""
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
    def __init__(self, path: Path, layout: dict[str, Placement]) -> None:
        self._path = path
        self._layout = layout
        self._lock = threading.Lock()
        self._unsaved = False
        self._settled = False

    def place(self, name: str, placement: Placement) -> None:
        with self._lock:
            self._layout[name] = placement
            self._unsaved = True

    def settle(self) -> None:
        with self._lock:
            self._settled = True

    def _write(self, *, settled_only: bool) -> None:
        with self._lock:
            if not (self._settled or (self._unsaved and not settled_only)):
                return
            snapshot = dict(self._layout)
            self._settled = self._unsaved = False
        write_layout(self._path, snapshot)

    def pump(self, stop: threading.Event, now: float) -> None:
        self._write(settled_only=True)

    def close(self) -> None:
        self._write(settled_only=False)


class _PointerDrawing:
    def __init__(self) -> None:
        self._laser = ScreenMesh()
        self._cursor = ScreenMesh()
        self._handles = [ScreenMesh() for _ in range(3)]
        self._visible: list[tuple[ScreenMesh, tuple[float, float, float, float]]] = []

    def update(self, frame: Frame, screens: Sequence[Screen]) -> None:
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

    def draw(self, renderer: SceneRenderer, view_proj: np.ndarray) -> None:
        for mesh, color in self._visible:
            renderer.draw_solid(mesh, view_proj, color)

    def close(self) -> None:
        for mesh in (self._laser, self._cursor, *self._handles):
            mesh.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    manifest = LaunchManifest.read(args.manifest)
    vr = VrSettings.read(args.manifest)
    # Before any window exists: the VR session is its own app on the taskbar.
    try:
        set_app_user_model_id(VR_APP_USER_MODEL_ID)
    except OSError:
        logger.debug("Could not claim the taskbar identity", exc_info=True)

    ready = vr_runtime.ensure_ready()
    if ready.readiness is not vr_runtime.Readiness.READY:
        logger.error("VR not available: %s", ready.readiness.value)
        _show_error_popup(vr_runtime.explain(ready))
        return 1
    return _run(manifest, vr)


def _unit_name(unit: object) -> str:
    side = getattr(unit, "side", "")
    return f"{type(unit).__name__}[{side}]" if side else type(unit).__name__


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


def _pump_channels(units: list, stop: threading.Event, perf: FramePerf) -> None:
    """The file-channel worker: every unit's flags, drains, status writes and
    repaints — file I/O that can stall under a sync client, so never the frame
    loop's thread.  Two threads on one mpv is its design; see player_core.mpv_gate.
    Guarded per unit: the OSR2 is driven from here (:class:`_PumpFaults`)."""
    period = 1.0 / PUMP_HZ
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


def tilt_from_stick(axis: float, elapsed_s: float) -> float:
    if abs(axis) <= CONTROLLER_DEADZONE:
        return 0.0
    return -axis * elapsed_s * TILT_RATE_DEG_S  # inverted: stick away lowers


def _update_quad_layer(
    session, renderer: SceneRenderer, index: int, unit: _VideoUnit,
    scene_yaw_deg: float, scene_pitch_deg: float,
):
    """Refresh *unit*'s quad swapchain if its texture moved, and describe the
    layer to submit — or None before the first frame of content exists."""
    from .vr_session import QuadLayer  # sibling of the lazy VRSession import

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
    """Fill both eyes with the cover: ground edge to edge, panel on top, and
    head-locked -- the projection matrix ALONE leaves out the head rotation the
    scene's view matrix carries."""
    for eye_index, view in enumerate(views):
        session.bind_eye_framebuffer(eye_index)
        renderer.begin_eye(COVER_CLEAR)
        if cover.screen.ready:
            projection = fov_to_projection_matrix(
                view.fov.angle_left, view.fov.angle_right,
                view.fov.angle_up, view.fov.angle_down,
                0.05, 100.0,
            )
            renderer.draw_screen(
                cover.screen.mesh, cover.texture.texture,
                np.ascontiguousarray(projection, dtype=np.float32),
            )
        session.release_eye_framebuffer(eye_index)


def _scene_is_up(primary, genau, satellites: Sequence, panel) -> bool:
    """Whether every picture the session opens with has reached its texture —
    what the orchestrator holds the cover for.  The main slot counts once."""
    main = genau.texture if genau.role.showing else primary.target
    return bool(
        main.ready
        and panel.texture.ready
        and all(satellite.target.ready for satellite in satellites)
    )


def _pointable_screens(satellites: Sequence[_SatelliteUnit], panel: _PanelUnit) -> list[Screen]:
    screens = []
    for unit in satellites:
        if not unit.target.ready:
            continue
        screens.append(Screen(unit.side, unit.screen.placement, unit.target.aspect,
                              movable=True, resizable=True, pressable=True))
        if unit.hud_ready:
            screens.append(Screen(hud_screen_name(unit.side), unit.hud_screen.placement,
                                  unit.hud_texture.aspect, pressable=True))
    if panel.texture.ready:
        screens.append(Screen(
            PANEL, panel.screen.placement, panel.texture.aspect, movable=True, pressable=True))
    return screens


def _draw_eyes(
    session,
    renderer: SceneRenderer,
    primary: _MainUnit,
    genau: _GenauUnit,
    satellites: list[_SatelliteUnit],
    panel: _PanelUnit,
    pointing: _PointerDrawing,
    views,
    mode: int | None,
    scene_rotation: np.ndarray,
    *,
    include_screens: bool,
) -> None:
    """Render the projection layer's two eyes: whichever main-slot player has
    the scene, as an immersive wrap or a screen; every other screen when the
    compositor-layer path is off (*include_screens*); the console panel and
    the pointer's chrome over all of it.  *scene_rotation* is where the
    arrangement sits."""
    clip_showing = genau.role.showing
    clip_mode = immersive_mode(genau.role.projection) if clip_showing else None
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
        if clip_showing and genau.texture.ready:
            if clip_mode is not None:
                inv32 = np.ascontiguousarray(np.linalg.inv(view_proj), dtype=np.float32)
                renderer.draw_immersive(clip_mode, genau.texture.texture, inv32, eye_index)
            elif genau.screen.ready:
                renderer.draw_screen(genau.screen.mesh, genau.texture.texture, view_proj32)
        elif not clip_showing and primary.target.ready and primary.role.displayed:
            if mode is not None:
                inv32 = np.ascontiguousarray(np.linalg.inv(view_proj), dtype=np.float32)
                renderer.draw_immersive(mode, primary.target.texture, inv32, eye_index)
            elif include_screens and primary.screen.ready:
                renderer.draw_screen(primary.screen.mesh, primary.target.texture, view_proj32)
        if include_screens:
            for satellite in satellites:
                if satellite.target.ready and satellite.screen.ready:
                    renderer.draw_screen(
                        satellite.screen.mesh, satellite.target.texture, view_proj32)
        for satellite in satellites:
            if satellite.hud_ready:
                renderer.draw_screen(
                    satellite.hud_screen.mesh, satellite.hud_texture.texture, view_proj32,
                    blend=True,
                )
        if panel.texture.ready and panel.screen.ready:
            renderer.draw_screen(panel.screen.mesh, panel.texture.texture, view_proj32, blend=True)
        pointing.draw(renderer, view_proj32)
        session.release_eye_framebuffer(eye_index)


# A couple of frames on a running compositor; past this, not worth a quit.
TEARDOWN_COVER_TIMEOUT_S = 1.0


def _cover_the_teardown(session, renderer: SceneRenderer, cover: _CoverUnit) -> None:
    """Raise the closing cover and hold it while this process comes apart.

    The ordinary quit asks through the shutdown progress file; this is the other
    way out — window closed, interrupt, a runtime that ended the session — where
    the units below would be watched closing one at a time.
    """
    cover.closing_now()
    try:
        cover.refresh()
        deadline = time.monotonic() + TEARDOWN_COVER_TIMEOUT_S
        while session.running and time.monotonic() < deadline:
            session.poll_events()
            if not session.session_ready:
                break  # nothing is being presented; there is nothing to cover
            should_render, display_time, views = session.frame_begin()
            cover.render_latest_frame()
            covered = bool(should_render and views and cover.showing)
            if covered:
                _draw_cover(session, renderer, cover, views)
            session.frame_end(display_time, views, project=covered)
            if covered:
                break
    except Exception:
        # A compositor that has let go of us must not turn a quit into a crash.
        logger.debug("Could not raise the closing cover", exc_info=True)
    cover.settled()  # however that went, teardown has waited long enough


def _run(manifest: LaunchManifest, vr: VrSettings) -> int:
    import glfw  # GL/XR stack loads only after the runtime probe
    import xr

    from .vr_session import VRSession

    bringup_deadline = time.monotonic() + SESSION_BRINGUP_TIMEOUT_S
    while True:
        try:
            session = VRSession()
            break
        except xr.exception.GraphicsDeviceInvalidError as exc:
            # A cold-started runtime answers the readiness probe before its
            # compositor's graphics device is up, and create_session in that
            # window fails with GRAPHICS_DEVICE_INVALID -- transient, so bring-up
            # waits it out instead of dying on the popup.
            if time.monotonic() >= bringup_deadline:
                logger.error("VR session bring-up failed: %s", exc)
                _show_error_popup(
                    "Could not start a VR session.\n\nThe VR runtime started, but its "
                    "graphics device never became ready.\n\nError: "
                    f"{exc}"
                )
                return 1
            logger.info("VR runtime's graphics device not ready yet; retrying bring-up")
            time.sleep(SESSION_BRINGUP_RETRY_S)
        except Exception as exc:
            logger.exception("VR session bring-up failed")
            _show_error_popup(
                "Could not start a VR session.\n\nThe headset answered, but FunTimeVR "
                f"could not open a session on it.\n\nError: {exc}"
            )
            return 1

    renderer = SceneRenderer()

    def get_proc_address(name: str):
        return glfw.get_proc_address(name)

    stop = threading.Event()
    commands = manifest.commands
    state_dir = Path(commands.dashboard_cmd_file).parent
    layout_path = state_dir / LAYOUT_FILENAME
    layout = read_layout(layout_path)
    primary = _MainUnit(manifest, vr, get_proc_address)
    genau = _GenauUnit(manifest, vr, stop)
    satellites = [
        _SatelliteUnit(side, manifest, get_proc_address, placement=layout[side])
        for side in (PORTRAIT, LANDSCAPE)
    ]
    panel = _PanelUnit(
        primary, genau, placement=layout[PANEL],
        dashboard_cmd_file=Path(commands.dashboard_cmd_file),
    )
    keeper = _LayoutKeeper(layout_path, layout)
    cover = _CoverUnit(state_dir)
    scene_ready = SceneReady(scene_ready_file(state_dir))
    units = [primary, genau, *satellites, panel, cover]
    pumped = [*units, keeper]
    hanging = {unit.side: unit.screen for unit in satellites} | {PANEL: panel.screen}
    pointer = Pointer()
    pointing = _PointerDrawing()
    use_layers = vr.compositor_layers
    perf = FramePerf(logger=logger)
    # The recentering yaw, with the role's tilt read in beside it each frame.
    scene_yaw = 0.0
    scene_rotation = np.eye(4, dtype=np.float32)
    last_frame_time = time.monotonic()
    pump_thread = start_daemon_thread(
        target=_pump_channels, args=(pumped, stop, perf), name="file-channels",
    )
    # Genau's engine on its own thread: file I/O every turn, at the desktop
    # window's rate rather than the pump's.
    genau_thread = start_daemon_thread(
        target=run_ticks, args=(genau.role, stop), name="genau-tick",
    )
    logger.info(
        "Entering the VR loop (four players up, compositor layers %s)",
        "on" if use_layers else "off",
    )

    try:
        while session.running and not stop.is_set():
            session.poll_events()
            if session.window_close_requested():
                break

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
            # The only place that sees the room fill in; it says so once.
            scene_ready.note(_scene_is_up(primary, genau, satellites, panel))
            t2 = time.perf_counter()

            quads = []
            project = False
            covered = False
            t3 = t2
            if should_render and views and cover.showing:
                # No quads: the runtime composites those OVER our layer, so a
                # screen submitted as one shows through the cover.
                covered = True
                project = True
                t3 = time.perf_counter()
                _draw_cover(session, renderer, cover, views)
            elif should_render and views:
                if session.focused:
                    primary.route_audio()
                if primary.role.take_recenter():
                    scene_yaw = yaw_of_orientation((
                        views[0].pose.orientation.x, views[0].pose.orientation.y,
                        views[0].pose.orientation.z, views[0].pose.orientation.w,
                    ))
                    logger.info(
                        "Recentered the scene onto heading %.0f°", math.degrees(scene_yaw)
                    )
                session.sync_controller(display_time)
                primary.role.nudge_tilt(
                    tilt_from_stick(session.thumbstick_y, frame_dt)
                )
                scene_pitch_deg = primary.role.tilt_deg
                scene_rotation = yaw_rotation_matrix(scene_yaw) @ pitch_rotation_matrix(
                    math.radians(scene_pitch_deg)
                )
                screens = _pointable_screens(satellites, panel)
                frame = pointer.frame(
                    session.hands,
                    head=head_position([
                        (view.pose.position.x, view.pose.position.y, view.pose.position.z)
                        for view in views
                    ]),
                    scene_rotation=scene_rotation,
                    screens=screens,
                )
                for name, placement in frame.moved.items():
                    hanging[name].placement = placement
                    keeper.place(name, placement)
                if frame.settled:
                    keeper.settle()
                for unit in (*satellites, panel):
                    unit.point(frame)
                pointing.update(frame, screens)
                mode = immersive_mode(primary.role.projection)
                if use_layers:
                    # The mpv-backed screens as quads; the primary stays in the
                    # projection layer while it wraps the view or the clip has the scene.
                    for index, unit in enumerate([primary, *satellites]):
                        if unit is primary and (mode is not None or genau.role.showing):
                            continue
                        quad = _update_quad_layer(
                            session, renderer, index, unit,
                            math.degrees(scene_yaw), scene_pitch_deg,
                        )
                        if quad is not None:
                            quads.append(quad)
                project = True  # the panel lives in the projection layer
                t3 = time.perf_counter()
                _draw_eyes(
                    session, renderer, primary, genau, satellites, panel, pointing, views, mode,
                    scene_rotation,
                    include_screens=not use_layers,
                )
            t4 = time.perf_counter()
            session.frame_end(display_time, views, project=project, quads=quads)
            t5 = time.perf_counter()
            if covered or not should_render:
                cover.settled()
            glfw.poll_events()
            perf.note("wait", (t1 - t0) * 1e3)
            perf.note("mpv", (t2 - t1) * 1e3)
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
        genau_thread.join(timeout=2.0)
        for unit in pumped:
            unit.close()
        pointing.close()
        renderer.close()
        session.close()
        logger.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
