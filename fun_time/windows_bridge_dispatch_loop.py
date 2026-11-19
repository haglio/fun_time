from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app_support.threading_utils import start_daemon_thread

from .arrivals import keep_looking
from .bridge_records import BridgeConfig
from .command_answering import CommandAnswering
from .command_bell import Bell, CommandBell, wait_out_the_tick
from .config import FunestraChannel
from .dispatch_loop_setup import LOOP_TICK_S, Publishing, SessionFiles, SessionStart
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import HandOvers
from .gallery import GalleryKeptOffTheLoop, gallery_database_in, the_librarys_origenerator
from .hosted_contract import when_said_of
from .hud_feed import HudFeed
from .latest_arrivals import LatestArrivals
from .loop_followers import (
    DashboardCommands,
    Follower,
    LoopParts,
    give_core_the_browsers_place_if_owed,
    the_followers,
)
from .main_list_builds import BuildsOffTheLoop
from .manifest import LaunchManifest
from .origenerator_opening import (
    OrigeneratorNeverCameUp,
    OrigeneratorOpening,
)
from .rfb_mode import with_the_browser_in_its_place
from .runtime_flow import hand_the_satellite_to_the_app
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .shared_state import BridgeState, read_shared_state
from .text_field_clicks import TypingWatch
from .window_work import WindowWork

logger = logging.getLogger(__name__)


LONGER_THAN_A_STALLED_DISK_HOLDS_A_TICK_S = 90.0


def detect_sleep_gap(prev_wall: float, now_wall: float, *,
                     threshold_s: float = LONGER_THAN_A_STALLED_DISK_HOLDS_A_TICK_S) -> float | None:
    # The thread freezes while Windows sleeps, and AHK's hotkeys are prone to not firing after a
    # wake.
    gap = now_wall - prev_wall
    return gap if gap >= threshold_s else None


class DispatchLoopRunner:
    def __init__(
        self,
        config: BridgeConfig,
        files: SessionFiles,
        start: SessionStart,
        publishing: Publishing,
        env: SessionEnvironment = ORDINARY_SESSION,
        *,
        followers: Callable[[LoopParts], Sequence[Follower]] = the_followers,
        clock: Callable[[], float] = time.monotonic,
        bell: Bell | None = None,
    ) -> None:
        left_in = read_shared_state(files.shared_state) or BridgeState()
        self.opening = OrigeneratorOpening(
            config, left_in,
            already_open=start.origenerator_already_open,
            showing_a_slideshow=start.origenerator_showing_a_slideshow)
        self.window_work = WindowWork(config, files, start, env)
        self.answering = CommandAnswering(
            config, files, state=with_the_browser_in_its_place(left_in),
            window_work=self.window_work, opening=self.opening,
            dashboard_enabled=publishing.dashboard_enabled, env=env, clock=clock)
        self.hud = HudFeed(config=config, publisher=publishing.hud_publisher)
        self.hand_overs = HandOvers()
        self.latest_arrivals = LatestArrivals(config)
        self._typing = TypingWatch(
            start.press_and_caret_watch,
            typed_into=lambda seen: self.window_work.windows.takes_typing(
                seen.window, seen.process))
        self.followers = tuple(followers(LoopParts(
            config=config, files=files, publishing=publishing, opening=self.opening,
            typing=self._typing, latest_arrivals=self.latest_arrivals, hud=self.hud,
            rfb_slideshow=start.rfb_slideshow)))
        self._clock = clock
        self._stop = threading.Event()
        self._dashboard_cmd = files.dashboard_cmd
        self._bell = bell

    def activate_origenerator(self, *, wait_for_the_app) -> None:
        if not self.opening.opens_now(wait_for_the_app):
            return
        answering = self.answering
        answering.state = self.opening.app_as_it_is(answering.state)
        config = answering.config
        self.hand_overs.expect(FunTimeFunestra.SATELLITES, config)
        for funestra, satellite in config.satellites.items():
            hand_the_satellite_to_the_app(funestra, satellite, config.origenerator.cmd_file)
        give_core_the_browsers_place_if_owed(self.opening, answering)
        if not wait_for_the_app(self.the_app_has_every_satellite):
            raise OrigeneratorNeverCameUp

    def the_app_has_every_satellite(self) -> bool:
        if self.hand_overs.taken(self.answering.config):
            self.hud.publish(self.answering.state)
        return not self.hand_overs.still_going()

    def tick(self) -> None:
        for follower in self.followers:
            follower.tick(self._clock(), self.answering)

    def run(self) -> None:
        self.window_work.log_topmost_state("startup", self.answering.state)
        self._typing.start()
        start_daemon_thread(target=keep_looking, args=(self.latest_arrivals.look, self._stop),
                            name="latest-arrivals")
        kept_gallery = self.answering.config.kept_gallery
        if kept_gallery is not None:
            start_daemon_thread(target=keep_looking, args=(kept_gallery.look, self._stop),
                                name="kept-gallery")
        bell = self._bell = self._bell or CommandBell(self._dashboard_cmd)
        between_ticks = DashboardCommands(self._dashboard_cmd)
        try:
            self._tick_until_stopped(bell, between_ticks)
        finally:
            bell.close()

    def _tick_until_stopped(self, bell: Bell, between_ticks: DashboardCommands) -> None:
        last_wall = time.time()
        while not self._stop.is_set():
            now = time.time()
            gap = detect_sleep_gap(last_wall, now)
            if gap is not None:
                logger.warning(
                    "Dispatch loop resumed after %.0fs stall — likely system "
                    "sleep/standby; AHK hotkeys may be dead until Fun Time is restarted",
                    gap,
                )
            last_wall = now
            try:
                self.tick()
            except Exception:
                logger.exception("Dispatch loop error")
            wait_out_the_tick(bell=bell, stop=self._stop, clock=self._clock, tick_s=LOOP_TICK_S,
                              answer=lambda: self._answer_between_ticks(between_ticks))

    def _answer_between_ticks(self, commands: DashboardCommands) -> None:
        try:
            commands.tick(self._clock(), self.answering)
        except Exception:
            logger.exception("Dispatch loop error answering a command between ticks")

    def stop(self) -> None:
        self._stop.set()
        if self._bell is not None:
            self._bell.ring_here()

    def close(self) -> None:
        self.window_work.close()
        self._typing.stop()


def build_bridge_config_from_manifest(
    manifest: LaunchManifest,
    *,
    vr_main_funestra: bool = False,
) -> BridgeConfig:
    commands = manifest.commands
    librarys_origenerator = the_librarys_origenerator()
    return BridgeConfig(
        vr_main_funestra=vr_main_funestra,
        main_list_builds=BuildsOffTheLoop(),
        portrait=manifest.portrait,
        landscape=manifest.landscape,
        trash_dir=Path(manifest.media.trash_dir),
        state_dir=Path(commands.state_dir),
        loopback_port=manifest.loopback_port,
        main_sources=manifest.media.main_funestra_library_sources,
        vr_library_dirs=manifest.media.vr_library_dirs,
        genau_vr_flicks=manifest.media.genau_vr_flicks,
        python_exe=manifest.executables.python_exe,
        broker_mode_file=Path(commands.broker_mode_file),
        audio_paused_file=Path(commands.audio_paused_file),
        audio_volume_file=Path(commands.audio_volume_file),
        dashboard_state_file=Path(commands.dashboard_state_file),
        main_funestra=FunestraChannel(
            cmd_file=Path(commands.main_funestra_cmd_file),
            paused_file=Path(commands.main_funestra_paused_file),
            status_file=Path(commands.main_funestra_status_file),
            notice_file=Path(commands.main_funestra_notice_file)),
        genau=FunestraChannel(
            cmd_file=Path(commands.genau_cmd_file),
            paused_file=Path(commands.genau_paused_file),
            status_file=Path(commands.genau_status_file)),
        origenerator=FunestraChannel(
            cmd_file=Path(commands.origenerator_cmd_file),
            paused_file=Path(commands.origenerator_paused_file),
            status_file=Path(commands.origenerator_status_file)),
        broker_cmd_file=Path(commands.broker_cmd_file),
        broker_state_dir=Path(v) if (v := commands.broker_state_dir.strip()) else None,
        broker_tray_launcher=Path(v) if (v := commands.broker_tray_launcher.strip()) else None,
        regen_media_root=Path(v) if (v := manifest.regen.media_root.strip()) else None,
        regen_metadata_root=manifest.regen.metadata_folder,
        regen_generate_video_url=manifest.regen.generate_video_url,
        regen_generate_image_url=manifest.regen.generate_image_url,
        librarys_origenerator=librarys_origenerator,
        kept_gallery=GalleryKeptOffTheLoop(gallery_database_in(librarys_origenerator)),
        origenerator_when_said=when_said_of(manifest.runtime.origenerator_dir.strip()),
    )
