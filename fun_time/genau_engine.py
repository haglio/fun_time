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
from funestra.broker_feed import BrokerFeed, udp_reader
from funestra.file_channel import read_paused_state
from funestra.flag import Flag
from funestra.flick_advance import FlickAdvanceState
from funestra.flick_cache import DecodeRequestState, FlickCacheStore
from funestra.flick_decode import load_flick_frames
from funestra.flick_flip import FlickFlip
from funestra.flick_folder import move_flick_to_weird
from funestra.flick_loader import FlickLoadController
from funestra.flick_renderer import FlickRenderController
from funestra.flick_selection import FlickSelectionController
from funestra.flick_sequence import FlickSequenceController
from funestra.funestra_verbs import LOCK_ON
from funestra.genau_controls import GenauControls, apply_runtime_command
from funestra.genau_refresh import GenauRefreshController
from funestra.learned_motion import LearnedMotionState, load_default_model
from funestra.robot_hand import RobotHandState, bpm_for_speed
from funestra.robot_hand_beat import BeatEngine
from funestra.robot_hand_driver import RobotHandTCodeDriver

logger = logging.getLogger(__name__)

CONDEMN_RETRY_S = 0.25
CONDEMN_PATIENCE_S = 30.0

Rescan = Callable[[bool], list[Path]]
Narrow = Callable[[bool, bool], list[Path]]


@dataclass(frozen=True)
class GenauEngine:
    controls: GenauControls
    controller: GenauRefreshController
    renderer: FlickRenderController
    driver: RobotHandTCodeDriver
    narrow: Callable[[Callable[[], list[Path]]], None]
    play_next: Callable[[Path], None]

    @property
    def current_flick(self) -> Path | None:
        return self.renderer.current_flick_path

    def playhead(self) -> tuple[int, int]:
        entry = self.renderer.current_flick_entry()
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
        self.controller.seek_the_flick(fraction)

    def seek_the_time_on_screen(self, seconds: float) -> None:
        self.controller.seek_the_time_on_screen(seconds)

    def close(self) -> None:
        self.driver.close()


def build_genau_engine(
    *,
    flicks: list[Path],
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
    say_no_other_flicks: Callable[[], None] = lambda: None,
    set_volume: Callable[[int, bool], None] | None = None,
    stop_event: threading.Event | None = None,
    start_flick: Path | None = None,
    metadata_root: Path | None = None,
    decode: Callable[[Path], list] = load_flick_frames,
    start_thread=start_daemon_thread,
    clock: Callable[[], float] = time.monotonic,
    log: logging.Logger = logger,
) -> GenauEngine:
    sequence = FlickSequenceController(flicks, start_at=start_flick)
    flick_store = FlickCacheStore(limit=settings.flick_cache_size)
    renderer = FlickRenderController(
        flick_store=flick_store,
        blit_frame=lambda frame: blit_frame(frame, renderer.current_flick_path))
    loader = FlickLoadController(
        flick_store=flick_store,
        load_state=DecodeRequestState(),
        prefetch_state=DecodeRequestState(),
        current_flick_path_getter=lambda: renderer.current_flick_path,
        decode_flick=decode,
        start_thread=start_thread,
        logger=log,
        on_active_flick_loaded=renderer.prepare_active_flick_for_current_size,
    )
    selection = FlickSelectionController(
        sequence=sequence,
        flick_store=flick_store,
        loader=loader,
        renderer=renderer,
        notifier=notifier,
        condemn_flick=lambda flick: _condemn(flick, condemned_to(flick), log, start_thread),
    )
    robot_hand = RobotHandState(playing=False, speed=50, bpm=bpm_for_speed(50))
    learned = LearnedMotionState(model=load_default_model())
    driver = RobotHandTCodeDriver(tcode_sink, robot_hand=robot_hand, learned=learned)
    broker = BrokerFeed()
    start_thread(
        target=udp_reader,
        args=(settings.udp_host, settings.udp_port, broker, stop_event or threading.Event(), log),
        name="genau-udp",
    )
    controls = GenauControls(
        engine=BeatEngine(last_tick=clock()),
        paused=Flag(),
        step_flick=selection.step,
        condemn_flick=selection.condemn_current,
        robot_hand=robot_hand,
        learned_motion_state=learned,
        flick_advance_state=FlickAdvanceState(),
        stop_event=stop_event,
        hud=Flag(),
        set_volume=set_volume,
        reorder_flicks=lambda recent: _reorder(selection, rescan, recent, log),
        keep_shapes=None if narrow is None else (
            lambda plays_vr, plays_flat: narrow_to(lambda: narrow(plays_vr, plays_flat))),
        play_file=selection.play,
        flick_flip=FlickFlip(metadata_root),
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

    def holding_what_is_on_screen() -> None:
        apply_runtime_command(LOCK_ON, controls)
        say_no_other_flicks()

    def narrow_to(scan: Callable[[], list[Path]]) -> None:
        _narrow(selection, scan, log, nothing_left=holding_what_is_on_screen)

    selection.set_current_flick(selection.current_path)
    return GenauEngine(controls=controls, controller=controller, renderer=renderer,
                       driver=driver, narrow=narrow_to, play_next=selection.play_next)


def _rescanned(rescan: Callable[[], list[Path]], log: logging.Logger) -> list[Path] | None:
    try:
        return rescan()
    except (OSError, RuntimeError):
        log.warning("Could not rescan the flicks folder; keeping the sequence", exc_info=True)
        return None


def _reorder(selection: FlickSelectionController, rescan: Rescan, recent: bool,
             log: logging.Logger) -> None:
    if flicks := _rescanned(lambda: rescan(recent), log):
        selection.reorder(flicks)
        log.info("Browsing %s (%d flicks)", "newest-first" if recent else "reshuffled", len(flicks))


def _narrow(selection: FlickSelectionController, scan: Callable[[], list[Path]],
            log: logging.Logger, *, nothing_left: Callable[[], None]) -> None:
    flicks = _rescanned(scan, log)
    if flicks:
        selection.narrow(flicks)
    elif flicks is not None:
        nothing_left()


def _condemn(flick: Path, weird_dir: Path, log: logging.Logger, start_thread) -> None:
    if not _still_open_after_trying(flick, weird_dir, log):
        return
    log.info("%s is still open; condemning it once it is let go", flick.name)
    start_thread(target=_condemn_once_let_go, args=(flick, weird_dir, log), name="genau-condemn")


def _condemn_once_let_go(flick: Path, weird_dir: Path, log: logging.Logger) -> None:
    give_up_at = time.monotonic() + CONDEMN_PATIENCE_S
    while time.monotonic() < give_up_at:
        time.sleep(CONDEMN_RETRY_S)
        if not _still_open_after_trying(flick, weird_dir, log):
            return
    log.error("%s was still open after %gs, so it was not condemned", flick.name, CONDEMN_PATIENCE_S)


def _still_open_after_trying(flick: Path, weird_dir: Path, log: logging.Logger) -> bool:
    try:
        landed = move_flick_to_weird(flick, weird_dir)
    except PermissionError:
        return True
    except OSError:
        log.error("Could not move %s to %s", flick.name, weird_dir, exc_info=True)
        return False
    if landed is None:
        log.info("Flick %s was already gone; nothing to condemn", flick.name)
    else:
        log.info("Condemned %s to %s", flick.name, weird_dir)
    return False
