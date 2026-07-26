"""The VR player process: three players composited into one OpenXR scene.

The desktop session runs Nau and two satellite processes, each owning a
window; an OpenXR runtime gives the headset to a single rendering process, so
in VR all three are surfaces of this one process.  Each keeps its desktop
sibling's whole contract — the playlist/command/paused/status file quartet —
so the orchestrator, dispatch loop, voice control and hybrid arbiter drive
them without knowing the display changed.  The satellites ARE the satellite
package's own session/verb/status/HUD code, running against offscreen players;
the primary is :class:`fun_time_vr.roles.PrimaryRole`, Nau's contract
in-process.

Per frame: pump every unit's file channels, let each mpv render its latest
frame into that unit's texture, then draw both eyes — the primary as an
immersive wrap or center screen by its projection, the satellites as floating
screens beside it, over it in painter's order.

Not unit-tested: this is the GL/OpenXR/mpv shell.  Everything it wires —
roles, scene geometry, matrices, projections — is tested pure, and the
offscreen mpv path is verified against the real DLL.
"""
from __future__ import annotations

import argparse
import configparser
import ctypes
import logging
import threading
import time
from pathlib import Path

import numpy as np

from player_core.file_channel import consume_command_file, read_paused_state
from player_core.playlist import read_playlist
from player_core.render_player import MpvRenderPlayer
from player_core.status import StatusWriter
from player_core.tcode import UdpTCodeSink
from player_core.tcode_driver import FunscriptTCodeDriver
from player_core.timeline import TIMELINE_HEIGHT, progress_bar_bgra
from player_core.volume import VolumeHud, VolumeHudPainter, chip_xy

from satellite.hud_overlay import HudOverlay
from satellite.runtime import apply_command as apply_satellite_command
from satellite.session import SatelliteSession
from satellite.status import status_fields as satellite_status_fields

from . import vr_runtime
from .matrices import fov_to_projection_matrix, pose_to_view_matrix
from .render import RenderTarget, SceneRenderer, immersive_mode
from .roles import PrimaryRole
from .scene import (
    PRIMARY_WIDTH_DEG,
    SATELLITE_ELEVATION_DEG,
    SATELLITE_WIDTH_DEG,
    satellite_center_azimuth,
    surface_vertices,
)

logger = logging.getLogger(__name__)

# Overlay ids shared with the desktop satellite (10 is its lock HUD).
_OV_SCRUBBER = 11
_OV_VOLUME = 12

# Longest texture side a video gets; an 8K master still renders, at a size the
# GPU can decode + composite twice per frame alongside two more players.
VIDEO_TARGET_CAP_PX = 4096

_MUTED_INDICATOR = VolumeHud(volume=0, muted=True)


def _show_error_popup(message: str) -> None:
    """A foreground Win32 error box — a hidden-launched process that just
    exits is indistinguishable from a crash."""
    mb_ok, mb_iconerror = 0x0, 0x10
    mb_setforeground, mb_topmost = 0x00010000, 0x00040000
    ctypes.windll.user32.MessageBoxW(
        None, message, "FunTimeVR", mb_ok | mb_iconerror | mb_setforeground | mb_topmost,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FunTimeVR player (one OpenXR scene, three players)")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="The windows bridge manifest INI the orchestrator wrote")
    return parser


def _read_manifest(path: Path) -> configparser.ConfigParser:
    manifest = configparser.ConfigParser()
    manifest.optionxform = str
    manifest.read(str(path), encoding="utf-8")
    return manifest


class _VideoUnit:
    """What every on-scene player shares: an offscreen mpv, a texture target,
    and the screen vertices rebuilt whenever the video's aspect changes."""

    def __init__(self, player) -> None:
        self.player = player
        self.target = RenderTarget()
        self.vertices: np.ndarray | None = None
        self._screen_azimuth = 0.0
        self._screen_width_deg = PRIMARY_WIDTH_DEG
        self._screen_elevation_deg = 0.0

    def _set_screen(self, azimuth_deg: float, width_deg: float, elevation_deg: float = 0.0) -> None:
        self._screen_azimuth = azimuth_deg
        self._screen_width_deg = width_deg
        self._screen_elevation_deg = elevation_deg

    def render_latest_frame(self) -> None:
        width, height = self.player.video_dims
        if width and height:
            scale = min(1.0, VIDEO_TARGET_CAP_PX / max(width, height))
            sized = (max(1, round(width * scale)), max(1, round(height * scale)))
            if sized != (self.target.width, self.target.height):
                self.target.ensure(*sized)
                self.vertices = surface_vertices(
                    self._screen_azimuth, self._screen_width_deg,
                    aspect=self.target.aspect,
                    center_elevation_deg=self._screen_elevation_deg,
                )
        if self.target.ready and self.player.has_new_frame:
            # flip_y: mpv renders top-left-origin; the scene samples GL
            # bottom-left convention (verified against a top-half-white clip).
            self.player.render(self.target.fbo, self.target.width, self.target.height, flip_y=True)

    def overlay_furniture(self, position_ms: float, duration_ms: float, volume_hud, painter) -> None:
        """The scrubber along the bottom and the volume chip at its right end,
        exactly the furniture the desktop players draw."""
        if not self.target.ready:
            return
        scrubber = progress_bar_bgra(position_ms, duration_ms, None, self.target.width)
        self.player.overlay(_OV_SCRUBBER, 0, self.target.height - scrubber.shape[0], scrubber)
        x, y = chip_xy(win_w=self.target.width, win_h=self.target.height, timeline_h=TIMELINE_HEIGHT)
        self.player.overlay(_OV_VOLUME, x, y, painter.bgra(volume_hud))

    def close(self) -> None:
        self.target.close()
        self.player.close()


class _PrimaryUnit(_VideoUnit):
    def __init__(self, manifest: configparser.ConfigParser, get_proc_address) -> None:
        # Muted at birth: the primary's sound belongs on the headset, and the
        # headset's sink cannot be trusted until the compositor is presenting
        # (see route_audio) — unmuted-on-default would blare the room speakers
        # for the whole warm-up instead.
        super().__init__(MpvRenderPlayer(get_proc_address, muted=True, loop_file=True))
        self._set_screen(0.0, PRIMARY_WIDTH_DEG)
        commands, vr = manifest["commands"], manifest["vr"]
        self.cmd_file = Path(commands["nau_cmd_file"])
        self.paused_file = Path(commands["nau_paused_file"])
        metadata_raw = manifest.get("regen", "metadata_root", fallback="").strip()
        driver = FunscriptTCodeDriver(
            UdpTCodeSink(vr["tcode_udp_host"], int(vr["tcode_udp_port"]))
        )
        self.role = PrimaryRole(
            player=self.player,
            driver=driver,
            playlist_file=Path(commands["nau_playlist_file"]),
            metadata_root=Path(metadata_raw) if metadata_raw else None,
            vr_dirs=tuple(
                Path(part) for part in vr["library_dirs"].split("|") if part.strip()
            ),
            start_paused=read_paused_state(self.paused_file, logger=logger),
        )
        self._audio_device = vr.get("audio_device", "").strip()
        self._audio_routed = False
        self._status_writer = StatusWriter(
            Path(commands["nau_status_file"]), lambda role: role.status_fields()
        )
        self._volume_painter = VolumeHudPainter()
        self._unhandled: set[str] = set()

    def route_audio(self) -> None:
        """Give the primary its sound on the first frame the headset presents.

        On the first headset run this routing happened at construction, while
        the compositor was still bringing the headset up — and the sink took
        the stream without consuming it, so mpv's audio clock (which the video
        clock follows) never ticked: every player alive, the primary frozen on
        frame 1 for the whole session.  Waiting for the first rendered frame
        means the runtime is actually presenting, with the headset's endpoints
        live; setting the device then also makes mpv reinitialize its audio
        chain fresh.  audio-fallback-to-null (player_core) backstops a sink
        that still refuses: silent playback rather than no playback.
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
                    logger.info("Verb not in the VR prototype yet: %s", keyword)
        self.role.tick(now)
        self._status_writer.write(self.role)
        self.overlay_furniture(
            self.role.position_ms, self.role.duration_ms,
            VolumeHud(volume=self.role.volume, muted=self.role.muted), self._volume_painter,
        )

    def close(self) -> None:
        self.role.close()  # closes driver + player
        self.target.close()


class _SatelliteUnit(_VideoUnit):
    def __init__(self, side: str, manifest: configparser.ConfigParser, get_proc_address) -> None:
        super().__init__(
            MpvRenderPlayer(get_proc_address, muted=True, loop_file=False, prefetch=True)
        )
        self._set_screen(
            satellite_center_azimuth(side), SATELLITE_WIDTH_DEG, SATELLITE_ELEVATION_DEG
        )
        commands = manifest["commands"]
        self.side = side
        self.cmd_file = Path(commands[f"{side}_cmd_file"])
        self.paused_file = Path(commands[f"{side}_paused_file"])
        self.playlist_file = Path(commands[f"{side}_playlist_file"])
        self.session = SatelliteSession(
            self._read_playlist(),
            player=self.player,
            start_paused=read_paused_state(self.paused_file, logger=logger),
        )
        self._status_writer = StatusWriter(
            Path(commands[f"{side}_status_file"]), satellite_status_fields
        )
        # The lock HUD panel fun_time publishes, composited into the video by
        # mpv — no mouse reaches it in VR, but the map itself carries over.
        self.hud = HudOverlay(
            hud_file=Path(commands[f"{side}_hud_file"]),
            command_file=Path(commands["dashboard_cmd_file"]),
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
        self.hud.tick()
        self.overlay_furniture(
            self.session.position_ms, self.session.duration_ms,
            _MUTED_INDICATOR, self._volume_painter,
        )

    def close(self) -> None:
        self.session.close()  # closes the player
        self.target.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    manifest = _read_manifest(args.manifest)

    ready = vr_runtime.ensure_ready()
    if ready.readiness is not vr_runtime.Readiness.READY:
        logger.error("VR not available: %s", ready.readiness.value)
        _show_error_popup(vr_runtime.explain(ready))
        return 1
    return _run(manifest)


def _run(manifest: configparser.ConfigParser) -> int:
    import glfw  # noqa: PLC0415 — GL/XR stack loads only after the runtime probe

    from .vr_session import VRSession  # noqa: PLC0415

    try:
        session = VRSession()
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

    primary = _PrimaryUnit(manifest, get_proc_address)
    satellites = [
        _SatelliteUnit("portrait", manifest, get_proc_address),
        _SatelliteUnit("landscape", manifest, get_proc_address),
    ]
    units: list[_VideoUnit] = [primary, *satellites]
    stop = threading.Event()
    logger.info("Entering the VR loop (three players up)")

    try:
        while session.running and not stop.is_set():
            session.poll_events()
            if session.window_close_requested():
                break
            now = time.monotonic()
            primary.pump(stop, now)
            for satellite in satellites:
                satellite.pump(stop, now)

            if not session.session_ready:
                # The channels above stay live while the headset warms up, so
                # the orchestrator sees status and playback the moment it asks.
                glfw.poll_events()
                time.sleep(0.01)
                continue

            should_render, display_time, views = session.frame_begin()
            for unit in units:
                unit.render_latest_frame()

            if should_render and views:
                primary.route_audio()
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
                    view_proj = projection_matrix @ view_matrix
                    mode = immersive_mode(primary.role.projection)
                    if primary.target.ready:
                        if mode is not None:
                            renderer.draw_immersive(
                                mode, primary.target.texture, np.linalg.inv(view_proj), eye_index,
                            )
                        elif primary.vertices is not None:
                            renderer.draw_screen(primary.vertices, primary.target.texture, view_proj)
                    for satellite in satellites:
                        if satellite.target.ready and satellite.vertices is not None:
                            renderer.draw_screen(
                                satellite.vertices, satellite.target.texture, view_proj,
                            )
                    session.release_eye_framebuffer(eye_index)
            session.frame_end(display_time, views)
            glfw.poll_events()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        for unit in units:
            unit.close()
        renderer.close()
        session.close()
        logger.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
