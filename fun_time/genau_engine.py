from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app_support.threading_utils import start_daemon_thread
from funestra.broker_feed import BrokerFeed, UdpListener, udp_reader
from funestra.file_channel import read_paused_state
from funestra.flag import Flag
from funestra.flick_advance import FlickAdvanceState
from funestra.flick_cache import DecodeRequestState, FlickCacheStore
from funestra.flick_decode import load_flick_frames
from funestra.flick_flip import FlickFlip
from funestra.flick_folder import move_flick_to_trash
from funestra.flick_loader import FlickLoadController, FlickLoadParts
from funestra.flick_renderer import FlickRenderController
from funestra.flick_selection import FlickSelectionController, FlickSelectionParts
from funestra.flick_sequence import FlickSequenceController
from funestra.funestra_verbs import LOCK_ON
from funestra.genau_controls import GenauControls, apply_runtime_command
from funestra.genau_refresh import GenauRefreshController
from funestra.genau_refresh_parts import GenauRefreshParts
from funestra.learned_motion import LearnedMotionState, load_default_model
from funestra.robot_hand import RobotHandState, bpm_for_speed
from funestra.robot_hand_beat import BeatEngine
from funestra.robot_hand_driver import RobotHandTCodeDriver

from .inclusion_pairs import PROJECTION

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


@dataclass(frozen=True)
class GenauChannels:
    command: Path
    paused: Path
    status: Path
    drive: Path


@dataclass(frozen=True)
class FlickLibrary:
    flicks: list[Path]
    start_flick: Path | None = None
    metadata_root: Path | None = None


@dataclass(frozen=True)
class GenauHost:
    notifier: object
    tcode_sink: object
    blit_frame: Callable[[object, Path | None], None]
    set_loading_text: Callable[[str | None], None]
    rescan: Rescan
    condemned_to: Callable[[Path], Path]
    narrow: Narrow | None = None
    say_no_other_flicks: Callable[[], None] = lambda: None
    say: Callable[[str], None] = lambda _words: None
    set_volume: Callable[[int, bool], None] | None = None
    tell_where_it_went: Callable[[Path, Path], None] = lambda _flick, _landed: None


@dataclass(frozen=True)
class EngineSeams:
    stop_event: threading.Event | None = None
    decode: Callable[[Path], list] = load_flick_frames
    start_thread: Callable = start_daemon_thread
    clock: Callable[[], float] = time.monotonic
    log: logging.Logger = logger


def build_genau_engine(
    settings,
    channels: GenauChannels,
    library: FlickLibrary,
    host: GenauHost,
    seams: EngineSeams | None = None,
) -> GenauEngine:
    seams = seams or EngineSeams()
    stop_event, decode, start_thread, clock, log = (
        seams.stop_event, seams.decode, seams.start_thread, seams.clock, seams.log)
    sequence = FlickSequenceController(library.flicks, start_at=library.start_flick)
    flick_store = FlickCacheStore(limit=settings.flick_cache_size)
    renderer = FlickRenderController(
        flick_store=flick_store,
        blit_frame=lambda frame: host.blit_frame(frame, renderer.current_flick_path))
    loader = FlickLoadController(FlickLoadParts(
        flick_store=flick_store,
        load_state=DecodeRequestState(),
        prefetch_state=DecodeRequestState(),
        current_flick_path_getter=lambda: renderer.current_flick_path,
        decode_flick=decode,
        start_thread=start_thread,
        logger=log,
        on_active_flick_loaded=renderer.prepare_active_flick_for_current_size,
    ))
    selection = FlickSelectionController(FlickSelectionParts(
        sequence=sequence,
        flick_store=flick_store,
        loader=loader,
        renderer=renderer,
        notifier=host.notifier,
        condemn_flick=lambda flick: _condemn(
            _Condemning(flick, host.condemned_to(flick), host.tell_where_it_went, log), start_thread),
    ))
    robot_hand = RobotHandState(playing=False, speed=50, bpm=bpm_for_speed(50))
    learned = LearnedMotionState(model=load_default_model())
    driver = RobotHandTCodeDriver(host.tcode_sink, robot_hand=robot_hand, learned=learned)
    broker = BrokerFeed()
    start_thread(
        target=udp_reader,
        args=(UdpListener(settings.udp_host, settings.udp_port, broker,
                          stop_event or threading.Event(), log),),
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
        set_volume=host.set_volume,
        reorder_flicks=lambda recent: _reorder(selection, host.rescan, recent, log),
        keep_shapes=None if host.narrow is None else (
            lambda plays_vr, plays_flat: narrow_to(
                lambda: host.narrow(plays_vr, plays_flat),
                says=PROJECTION.flash((plays_flat, plays_vr)))),
        play_file=selection.play,
        flick_flip=FlickFlip(library.metadata_root),
    )
    controller = GenauRefreshController(GenauRefreshParts(
        controls=controls,
        broker=broker,
        loader=loader,
        notifier=host.notifier,
        renderer=renderer,
        selection=selection,
        command_file=Path(channels.command),
        paused_file=Path(channels.paused),
        beats_per_loop=settings.beats_per_loop,
        bpm_smoothing=settings.bpm_smoothing,
        sync_strength=settings.sync_strength,
        set_loading_text=host.set_loading_text,
        logger=log,
        now_source=clock,
        read_paused_state=read_paused_state,
        tcode_sender=driver,
        status_file=channels.status,
        drive_file=Path(channels.drive),
    ))

    def holding_what_is_on_screen() -> None:
        apply_runtime_command(LOCK_ON, controls)
        host.say_no_other_flicks()

    def narrow_to(scan: Callable[[], list[Path]], *, says: str = "") -> None:
        _narrow(selection, scan, log, nothing_left=holding_what_is_on_screen,
                kept=lambda: host.say(says) if says else None)

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
            log: logging.Logger, *, nothing_left: Callable[[], None],
            kept: Callable[[], object]) -> None:
    flicks = _rescanned(scan, log)
    if flicks:
        selection.narrow(flicks)
        kept()
    elif flicks is not None:
        nothing_left()


@dataclass(frozen=True)
class _Condemning:
    flick: Path
    trash_dir: Path
    tell_where_it_went: Callable[[Path, Path], None]
    log: logging.Logger


def _condemn(condemning: _Condemning, start_thread) -> None:
    if not _still_open_after_trying(condemning):
        return
    condemning.log.info("%s is still open; condemning it once it is let go", condemning.flick.name)
    start_thread(target=_condemn_once_let_go, args=(condemning,), name="genau-condemn")


def _condemn_once_let_go(condemning: _Condemning) -> None:
    give_up_at = time.monotonic() + CONDEMN_PATIENCE_S
    while time.monotonic() < give_up_at:
        time.sleep(CONDEMN_RETRY_S)
        if not _still_open_after_trying(condemning):
            return
    condemning.log.error("%s was still open after %gs, so it was not condemned",
                         condemning.flick.name, CONDEMN_PATIENCE_S)


def _still_open_after_trying(condemning: _Condemning) -> bool:
    flick, trash_dir, log = condemning.flick, condemning.trash_dir, condemning.log
    try:
        landed = move_flick_to_trash(flick, trash_dir)
    except PermissionError:
        return True
    except OSError:
        log.error("Could not move %s to %s", flick.name, trash_dir, exc_info=True)
        return False
    if landed is None:
        log.info("Flick %s was already gone; nothing to condemn", flick.name)
    else:
        log.info("Condemned %s to %s", flick.name, trash_dir)
        condemning.tell_where_it_went(flick, landed)
    return False
