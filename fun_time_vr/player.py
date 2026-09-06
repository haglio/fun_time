"""The VR player process: the session's four players composited into one
OpenXR scene.

The desktop session runs Nau, Genau and two satellite processes, each owning
a window; an OpenXR runtime gives the headset to a single rendering process,
so in VR all four are surfaces of this one process.  Each keeps its desktop
sibling's whole contract — the playlist/command/paused/status file quartet —
so the orchestrator, dispatch loop, voice control and device arbiter drive
them without knowing the display changed.  The satellites ARE the satellite
package's own session/verb/status/HUD code, running against offscreen players;
the main player is :class:`fun_time_vr.roles.MainRole`, Nau's contract
in-process; Genau is :class:`fun_time_vr.genau_role.GenauRole`, the clip
player's engine on a thread of its own, taking the scene in genau mode and
driving beneath the video in video mode.  The console hangs in the scene as
a panel of its own (:mod:`fun_time_vr.console_panel`).

Two threads.  A worker owns every file channel — pause flags, command drains,
status writes, HUD polls, and the in-video furniture repaints — at its own
cadence, because file I/O under a sync client can stall for arbitrary
milliseconds and none of it may ride the frame loop.  The render thread owns
GL: per frame it waits on the compositor, lets each mpv render its latest
frame into that unit's texture (only when one is newly due — the videos'
24-30fps never paces the 90Hz loop), and hands the compositor its layers.

With ``vr.compositor_layers=true``, flat screens (the satellites always, the
primary when its projection is ``flat``) are submitted as compositor quad
layers — the runtime places each in the world at the true head pose every
refresh, the architecture of the desktop-overlay tools.  Off by default: the
bundled "Pimax OpenXR 0.1.0" runtime accepts the quads in xrEndFrame and
never composites them, so screens submitted that way don't appear; everything
draws in-scene inside the projection layer instead, which every runtime
composites.

The GL/OpenXR/mpv shell.  Everything it wires — roles, scene geometry,
matrices, projections, furniture throttling — is tested pure, and the whole
pipeline minus OpenXR runs against the real DLLs in the hidden-desktop
integration suite.  See CLAUDE.md, "Standing rules".
"""
from __future__ import annotations

import argparse
import configparser
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from app_support.threading_utils import start_daemon_thread
from app_support.win32 import set_app_user_model_id
from player_core.console_hud import ConsolePainter
from player_core.file_channel import consume_command_file, read_paused_state
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
from .console_panel import (
    PANEL_AZIMUTH_DEG,
    PANEL_ELEVATION_DEG,
    PANEL_WIDTH_DEG,
    paint_panel,
    panel_hud,
)
from .furniture import chip_state, scrubber_state
from .genau_role import GenauRole, run_ticks
from .genau_settings import GenauSettings
from .matrices import (
    fov_to_projection_matrix,
    pitch_rotation_matrix,
    pose_to_view_matrix,
    yaw_of_orientation,
    yaw_rotation_matrix,
)
from .perf import FramePerf
from .render import FrameTexture, RenderTarget, SceneRenderer, ScreenMesh, immersive_mode
from .roles import MainRole
from .scene import (
    PRIMARY_WIDTH_DEG,
    SATELLITE_ELEVATION_DEG,
    SATELLITE_WIDTH_DEG,
    quad_layer_placement,
    satellite_center_azimuth,
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

# GenauVR's rate and deadzone; stick away raises, as TILT_UP does.
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
    notify_port: int = 50556
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
            notify_port=int(vr.get("notify_port", "50556")),
            genau=GenauSettings.from_manifest(vr),
        )


class _VideoUnit:
    """What every on-scene player shares: an offscreen mpv, a texture target,
    and the screen geometry rebuilt whenever the video's aspect changes."""

    def __init__(self, player, target_cap_px: int) -> None:
        self.player = player
        self.target = RenderTarget()
        self.mesh: ScreenMesh | None = None
        self._target_cap_px = target_cap_px
        self._screen_azimuth = 0.0
        self._screen_width_deg = PRIMARY_WIDTH_DEG
        self._screen_elevation_deg = 0.0
        # Compositor-layer bookkeeping, render-thread-owned: whether the
        # target holds pixels its quad swapchain hasn't copied yet, and the
        # size the swapchain's content was copied at (None until the first
        # copy, and again after a resize makes the copied image stale).
        self.layer_dirty = False
        self.layer_rect: tuple[int, int] | None = None
        # Furniture last painted, pump-thread-owned.
        self._scrubber_shown: tuple | None = None
        self._chip_shown: tuple | None = None

    def _set_screen(self, azimuth_deg: float, width_deg: float, elevation_deg: float = 0.0) -> None:
        self._screen_azimuth = azimuth_deg
        self._screen_width_deg = width_deg
        self._screen_elevation_deg = elevation_deg

    def render_latest_frame(self) -> None:
        width, height = self.player.video_dims
        if width and height:
            scale = min(1.0, self._target_cap_px / max(width, height))
            sized = (max(1, round(width * scale)), max(1, round(height * scale)))
            if sized != (self.target.width, self.target.height):
                self.target.ensure(*sized)
                if self.mesh is None:
                    self.mesh = ScreenMesh()
                self.mesh.upload(surface_vertices(
                    self._screen_azimuth, self._screen_width_deg,
                    aspect=self.target.aspect,
                    center_elevation_deg=self._screen_elevation_deg,
                ))
                self.layer_rect = None
        if self.target.ready and self.player.has_new_frame:
            # flip_y: mpv renders top-left-origin; the scene samples GL
            # bottom-left convention (verified against a top-half-white clip).
            self.player.render(self.target.fbo, self.target.width, self.target.height, flip_y=True)
            self.layer_dirty = True

    def layer_placement(self, scene_yaw_deg: float = 0.0, scene_pitch_deg: float = 0.0):
        """Pose and size for this screen's compositor quad, at the aspect its
        swapchain last copied; the scene angles turn and tilt the arrangement."""
        width, height = self.layer_rect
        return quad_layer_placement(
            self._screen_azimuth, self._screen_width_deg,
            aspect=width / height,
            center_elevation_deg=self._screen_elevation_deg,
            scene_yaw_deg=scene_yaw_deg,
            scene_pitch_deg=scene_pitch_deg,
        )

    def overlay_furniture(self, position_ms: float, duration_ms: float, volume_hud, painter) -> None:
        """The scrubber along the bottom and the volume chip at its right end,
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
        if self.mesh is not None:
            self.mesh.close()


class _MainUnit(_VideoUnit):
    def __init__(self, manifest: LaunchManifest, vr: VrSettings, get_proc_address) -> None:
        # Muted at birth: the headset's sink cannot be trusted until the
        # compositor is presenting (see route_audio).
        super().__init__(
            MpvRenderPlayer(get_proc_address, muted=True, loop_file=True),
            PRIMARY_VIDEO_CAP_PX,
        )
        self._set_screen(0.0, PRIMARY_WIDTH_DEG)
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
        self._audio_device = vr.audio_device.strip()
        self._audio_routed = False
        self._status_writer = StatusWriter(
            Path(commands.nau_status_file), lambda role: role.status_fields()
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
                    logger.info("Verb the VR main role does not handle: %s", keyword)
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
    def __init__(self, side: str, manifest: LaunchManifest, get_proc_address) -> None:
        # audio=False, not merely muted: any audio chain here can wedge on the
        # headset's parked endpoint and freeze the video clock (see route_audio).
        super().__init__(
            MpvRenderPlayer(
                get_proc_address, muted=True, loop_file=False, prefetch=True, audio=False,
            ),
            SATELLITE_VIDEO_CAP_PX,
        )
        self._set_screen(
            satellite_center_azimuth(side), SATELLITE_WIDTH_DEG, SATELLITE_ELEVATION_DEG
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
        # The lock HUD panel fun_time publishes, composited into the video by
        # mpv — no mouse reaches it in VR, but the map itself carries over.
        self.hud = HudOverlay(
            hud_file=Path(commands.side_file(side, "hud")),
            command_file=Path(commands.dashboard_cmd_file),
            player=self.player,
        )
        self._volume_painter = VolumeHudPainter()

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
        self.overlay_furniture(
            self.session.position_ms, self.session.duration_ms,
            _MUTED_INDICATOR, self._volume_painter,
        )

    def close(self) -> None:
        self.session.close()  # closes the player
        self._close_graphics()


class _GenauUnit:
    """Genau's surface: the frame its engine chose, on the primary's screen or
    wrapped round the viewer by the clip's projection.  No mpv behind it and
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
        self.mesh: ScreenMesh | None = None
        self._mesh_aspect: float | None = None

    def render_latest_frame(self) -> None:
        frame = self.role.take_frame()
        if frame is None:
            return
        self.texture.upload(frame)
        if self.mesh is None or self._mesh_aspect != self.texture.aspect:
            if self.mesh is None:
                self.mesh = ScreenMesh()
            self.mesh.upload(surface_vertices(0.0, PRIMARY_WIDTH_DEG, aspect=self.texture.aspect))
            self._mesh_aspect = self.texture.aspect

    def pump(self, stop: threading.Event, now: float) -> None:
        """Nothing: the engine turns its channels on its own thread."""

    def close(self) -> None:
        self.role.close()
        self.texture.close()
        if self.mesh is not None:
            self.mesh.close()


class _PanelUnit:
    """The console, hung in the scene: painted on the pump thread, uploaded on
    the render thread when it changed."""

    def __init__(self, primary: _MainUnit, genau: _GenauUnit) -> None:
        self._primary = primary
        self._genau = genau
        self._painter = ConsolePainter()
        self._chip_painter = VolumeHudPainter()
        self._lock = threading.Lock()
        self._image = None
        self._key = None
        self._width = 320
        self._uploaded = None
        self.texture = FrameTexture()
        self.mesh: ScreenMesh | None = None
        self._mesh_aspect: float | None = None

    def pump(self, stop: threading.Event, now: float) -> None:
        genau, main = self._genau.role, self._primary.role
        clip = genau.current_clip
        hud = panel_hud(
            genau.console_hud,
            video_title=main.current_video.stem,
            clip_title=clip.stem if clip is not None else "",
            loading=genau.loading,
        )
        if genau.showing:
            scrubber = None
            chip = VolumeHud(volume=genau.volume, muted=genau.muted)
        else:
            scrubber = (main.position_ms, main.duration_ms)
            chip = VolumeHud(volume=main.volume, muted=main.muted)
        # Repainted only when what it shows moves, as the furniture is.
        key = (
            hud,
            scrubber_state(self._width, 1, *scrubber) if scrubber is not None else None,
            chip,
        )
        if key == self._key:
            return
        image = paint_panel(
            self._painter, hud, scrubber=scrubber, chip=chip, chip_painter=self._chip_painter,
        )
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
        if self.mesh is None or self._mesh_aspect != self.texture.aspect:
            if self.mesh is None:
                self.mesh = ScreenMesh()
            self.mesh.upload(surface_vertices(
                PANEL_AZIMUTH_DEG, PANEL_WIDTH_DEG,
                aspect=self.texture.aspect,
                center_elevation_deg=PANEL_ELEVATION_DEG,
            ))
            self._mesh_aspect = self.texture.aspect

    def close(self) -> None:
        self.texture.close()
        if self.mesh is not None:
            self.mesh.close()


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


def _pump_channels(units: list, stop: threading.Event, perf: FramePerf) -> None:
    """The file-channel worker: every unit's flags, drains, status writes and
    repaints.  File I/O that can stall under a sync client, so never the frame
    loop's thread; libmpv's client API on one thread and its render API on
    another is its designed usage."""
    period = 1.0 / PUMP_HZ
    while not stop.is_set():
        started = time.monotonic()
        for unit in units:
            unit.pump(stop, started)
        perf.note("pump", (time.monotonic() - started) * 1e3)
        perf.maybe_flush()
        stop.wait(max(0.0, period - (time.monotonic() - started)))


def tilt_from_stick(axis: float, elapsed_s: float) -> float:
    if abs(axis) <= CONTROLLER_DEADZONE:
        return 0.0
    return axis * elapsed_s * TILT_RATE_DEG_S


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


def _draw_eyes(
    session,
    renderer: SceneRenderer,
    primary: _MainUnit,
    genau: _GenauUnit,
    satellites: list[_SatelliteUnit],
    panel: _PanelUnit,
    views,
    mode: int | None,
    scene_rotation: np.ndarray,
    *,
    include_screens: bool,
) -> None:
    """Render the projection layer's two eyes: whichever main-slot player has
    the scene, as an immersive wrap or a screen; every other screen when the
    compositor-layer path is off (*include_screens*); the console panel over
    all of it.  *scene_rotation* is where the arrangement sits."""
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
            elif genau.mesh is not None and genau.mesh.ready:
                renderer.draw_screen(genau.mesh, genau.texture.texture, view_proj32)
        elif not clip_showing and primary.target.ready and primary.role.displayed:
            if mode is not None:
                inv32 = np.ascontiguousarray(np.linalg.inv(view_proj), dtype=np.float32)
                renderer.draw_immersive(mode, primary.target.texture, inv32, eye_index)
            elif include_screens and primary.mesh is not None and primary.mesh.ready:
                renderer.draw_screen(primary.mesh, primary.target.texture, view_proj32)
        if include_screens:
            for satellite in satellites:
                if satellite.target.ready and satellite.mesh is not None and satellite.mesh.ready:
                    renderer.draw_screen(satellite.mesh, satellite.target.texture, view_proj32)
        if panel.texture.ready and panel.mesh is not None and panel.mesh.ready:
            renderer.draw_screen(panel.mesh, panel.texture.texture, view_proj32, blend=True)
        session.release_eye_framebuffer(eye_index)


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
    primary = _MainUnit(manifest, vr, get_proc_address)
    genau = _GenauUnit(manifest, vr, stop)
    satellites = [
        _SatelliteUnit("portrait", manifest, get_proc_address),
        _SatelliteUnit("landscape", manifest, get_proc_address),
    ]
    panel = _PanelUnit(primary, genau)
    units = [primary, genau, *satellites, panel]
    use_layers = vr.compositor_layers
    perf = FramePerf(logger=logger)
    # The recentering yaw, with the role's tilt read in beside it each frame.
    scene_yaw = 0.0
    scene_rotation = np.eye(4, dtype=np.float32)
    last_frame_time = time.monotonic()
    pump_thread = start_daemon_thread(
        target=_pump_channels, args=(units, stop, perf), name="file-channels",
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
            t2 = time.perf_counter()

            quads = []
            project = False
            t3 = t2
            if should_render and views:
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
                session.sync_controller()
                primary.role.nudge_tilt(
                    tilt_from_stick(session.thumbstick_y, frame_dt)
                )
                scene_pitch_deg = primary.role.tilt_deg
                scene_rotation = yaw_rotation_matrix(scene_yaw) @ pitch_rotation_matrix(
                    math.radians(scene_pitch_deg)
                )
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
                    session, renderer, primary, genau, satellites, panel, views, mode,
                    scene_rotation,
                    include_screens=not use_layers,
                )
            t4 = time.perf_counter()
            session.frame_end(display_time, views, project=project, quads=quads)
            t5 = time.perf_counter()
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
        stop.set()
        pump_thread.join(timeout=2.0)
        genau_thread.join(timeout=2.0)
        for unit in units:
            unit.close()
        renderer.close()
        session.close()
        logger.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
