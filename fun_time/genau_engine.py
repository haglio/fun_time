"""Genau's engine, built once for whichever surface shows it: the Main Funestra's
window, or the headset."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app_support.threading_utils import start_daemon_thread
from player_core.broker_feed import BrokerFeed, udp_reader
from player_core.clip_advance import ClipAdvanceState
from player_core.clip_cache import ClipCacheStore, DecodeRequestState
from player_core.clip_decode import load_clip_frames
from player_core.clip_flip import ClipFlip
from player_core.clip_folder import move_clip_to_weird
from player_core.clip_loader import ClipLoadController
from player_core.clip_renderer import ClipRenderController
from player_core.clip_selection import ClipSelectionController
from player_core.clip_sequence import ClipSequenceController
from player_core.cruise_control import CruiseControlState
from player_core.file_channel import read_paused_state
from player_core.flag import Flag
from player_core.genau_controls import GenauControls
from player_core.genau_refresh import GenauRefreshController
from player_core.learned_motion import LearnedMotionState, load_default_model
from player_core.robot_hand import RobotHandState, bpm_for_speed
from player_core.robot_hand_beat import BeatEngine
from player_core.robot_hand_driver import RobotHandTCodeDriver

logger = logging.getLogger(__name__)

Rescan = Callable[[bool], list[Path]]
Narrow = Callable[[bool, bool], list[Path]]


@dataclass(frozen=True)
class GenauEngine:
    controls: GenauControls
    controller: GenauRefreshController
    renderer: ClipRenderController
    driver: RobotHandTCodeDriver

    @property
    def current_clip(self) -> Path | None:
        return self.renderer.current_clip_path

    def playhead(self) -> tuple[int, int]:
        entry = self.renderer.current_clip_entry()
        frames = entry.get("frames") if entry else None
        count = len(frames) if frames else 0
        index = self.renderer.current_frame_index
        return (0 if index is None else max(0, count - 1 - index), count)

    @property
    def time_on_screen(self) -> tuple[float, float]:
        advance = self.controls.flick_advance_state
        return advance.elapsed, float(advance.interval)

    def refresh(self) -> None:
        self.controller.refresh()

    def seek(self, fraction: float) -> None:
        self.controller.seek_the_clip(fraction)

    def seek_the_time_on_screen(self, seconds: float) -> None:
        self.controller.seek_the_time_on_screen(seconds)

    def close(self) -> None:
        self.driver.close()


def build_genau_engine(
    *,
    clips: list[Path],
    settings,
    command_file: Path,
    paused_file: Path,
    status_file: Path | None,
    drive_file: Path,
    notifier,
    tcode_sink,
    blit_frame: Callable[[object, Path | None], None],
    set_loading_text: Callable[[str | None], None],
    rescan: Rescan,
    condemned_to: Callable[[Path], Path],
    narrow: Narrow | None = None,
    set_volume: Callable[[int, bool], None] | None = None,
    stop_event: threading.Event | None = None,
    start_clip: Path | None = None,
    metadata_root: Path | None = None,
    decode: Callable[[Path], list] = load_clip_frames,
    start_thread=start_daemon_thread,
    clock: Callable[[], float] = time.monotonic,
    log: logging.Logger = logger,
) -> GenauEngine:
    sequence = ClipSequenceController(clips, start_at=start_clip)
    clip_store = ClipCacheStore(limit=settings.clip_cache_size)
    renderer = ClipRenderController(
        clip_store=clip_store,
        blit_frame=lambda frame: blit_frame(frame, renderer.current_clip_path))
    loader = ClipLoadController(
        clip_store=clip_store,
        load_state=DecodeRequestState(),
        prefetch_state=DecodeRequestState(),
        current_clip_path_getter=lambda: renderer.current_clip_path,
        decode_clip=decode,
        start_thread=start_thread,
        logger=log,
        on_active_clip_loaded=renderer.prepare_active_clip_for_current_size,
    )
    selection = ClipSelectionController(
        sequence=sequence,
        clip_store=clip_store,
        loader=loader,
        renderer=renderer,
        notifier=notifier,
        condemn_clip=lambda clip: _condemn(clip, condemned_to(clip), log),
    )
    robot_hand = RobotHandState(playing=False, speed=50, bpm=bpm_for_speed(50))
    cruise = CruiseControlState()
    learned = LearnedMotionState(model=load_default_model())
    driver = RobotHandTCodeDriver(tcode_sink, robot_hand=robot_hand, cruise=cruise, learned=learned)
    broker = BrokerFeed()
    start_thread(
        target=udp_reader,
        args=(settings.udp_host, settings.udp_port, broker, stop_event or threading.Event(), log),
        name="genau-udp",
    )
    controls = GenauControls(
        engine=BeatEngine(last_tick=clock()),
        paused=Flag(),
        step_clip=selection.step,
        condemn_clip=selection.condemn_current,
        robot_hand=robot_hand,
        cruise_control_state=cruise,
        learned_motion_state=learned,
        set_motion_phase=driver.set_motion_phase,
        clip_advance_state=ClipAdvanceState(),
        stop_event=stop_event,
        hud=Flag(),
        set_volume=set_volume,
        reorder_clips=lambda recent: _reorder(selection, rescan, recent, log),
        keep_shapes=None if narrow is None else (
            lambda plays_vr, plays_flat: _narrow(selection, narrow, plays_vr, plays_flat, log)),
        play_file=selection.play,
        clip_flip=ClipFlip(metadata_root),
    )
    controller = GenauRefreshController(
        controls=controls,
        broker=broker,
        loader=loader,
        notifier=notifier,
        renderer=renderer,
        selection=selection,
        command_file=Path(command_file),
        paused_file=Path(paused_file),
        beats_per_loop=settings.beats_per_loop,
        bpm_smoothing=settings.bpm_smoothing,
        sync_strength=settings.sync_strength,
        set_loading_text=set_loading_text,
        logger=log,
        now_source=clock,
        read_paused_state=read_paused_state,
        tcode_sender=driver,
        status_file=status_file,
        drive_file=Path(drive_file),
    )
    selection.set_current_clip(selection.current_path)
    return GenauEngine(controls=controls, controller=controller, renderer=renderer,
                       driver=driver)


def _rescanned(rescan: Callable[[], list[Path]], log: logging.Logger) -> list[Path]:
    try:
        return rescan()
    except (OSError, RuntimeError):
        log.warning("Could not rescan the clips folder; keeping the sequence", exc_info=True)
        return []


def _reorder(selection: ClipSelectionController, rescan: Rescan, recent: bool,
             log: logging.Logger) -> None:
    if clips := _rescanned(lambda: rescan(recent), log):
        selection.reorder(clips)
        log.info("Browsing %s (%d clips)", "newest-first" if recent else "reshuffled", len(clips))


def _narrow(selection: ClipSelectionController, narrow: Narrow, plays_vr: bool,
            plays_flat: bool, log: logging.Logger) -> None:
    if clips := _rescanned(lambda: narrow(plays_vr, plays_flat), log):
        selection.narrow(clips)


def _condemn(clip: Path, weird_dir: Path, log: logging.Logger) -> None:
    try:
        landed = move_clip_to_weird(clip, weird_dir)
    except OSError:
        log.error("Could not move %s to %s", clip.name, weird_dir, exc_info=True)
        return
    if landed is None:
        log.info("Clip %s was already gone; nothing to condemn", clip.name)
    else:
        log.info("Condemned %s to %s", clip.name, weird_dir)
