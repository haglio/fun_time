from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from funestra.on_screen_history import OnScreenHistory

from .command_value import Command
from .config import FunestraChannel
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import GenauStatus, read_genau_status, read_main_funestra_status
from .satellite_control import SatelliteStatus, read_satellite_status
from .vr_main_funestra_verbs import VR_MAIN_FUNESTRA_VERBS
from .watch_stats import WatchTracker, record_watch_event

SAMPLE_INTERVAL_S = 0.5

NAV_COMMANDS: dict[int, frozenset[str]] = {
    1: frozenset({"main_prev", "main_next", "genau_prev_flick", "genau_next_flick"}),
    2: frozenset({"portrait_prev", "portrait_next", "portrait_cycle_action", "portrait_cycle_seed"}),
    3: frozenset({"landscape_prev", "landscape_next", "landscape_cycle_action", "landscape_cycle_seed"}),
}

DISCARD_COMMANDS: dict[str, int] = {"portrait_trash": 2, "landscape_trash": 3,
                                     "main_trash": 1, "genau_trash_flick": 1}

_NOTHING_BEING_WATCHED = ("", 0.0)


def _how_long_it_has_played(status: SatelliteStatus) -> float | None:
    if status.image or status.fraction is None:
        return None
    return status.position_ms / 1000


def _how_long_it_has_been_up(status: GenauStatus, now: float) -> float | None:
    return None if status.up_at is None else max(0.0, now - status.up_at)


class WatchSampler:
    def __init__(
        self,
        *,
        funestras: Mapping[FunTimeFunestra, FunestraChannel],
        genau: FunestraChannel,
        stats_file: Path,
    ) -> None:
        self._main_funestra_status_file = funestras[FunTimeFunestra.MAIN].status_file
        self._genau_status_file = genau.status_file
        self._satellite_status_files = {
            funestra: funestras[funestra].status_file for funestra in FunTimeFunestra.SATELLITES}
        self.stats_file = stats_file
        self._trackers: dict[int, WatchTracker] = {1: WatchTracker(), 2: WatchTracker(),
                                                   3: WatchTracker()}
        self._on_screen: dict[int, OnScreenHistory] = {1: OnScreenHistory(), 2: OnScreenHistory(),
                                                       3: OnScreenHistory()}
        self._last_sample = 0.0

    def sample_due(self, *, now: float, omni_paused: bool, genau_on_screen: bool = False) -> None:
        if now - self._last_sample < SAMPLE_INTERVAL_S:
            return
        self._last_sample = now
        if omni_paused:
            return
        self._sample_satellites(now=now)
        if genau_on_screen:
            self._sample_genau(now=now)
        else:
            self._sample_main(now=now)

    def _sample_satellites(self, *, now: float) -> None:
        for which, status_file in self._satellite_status_files.items():
            status = read_satellite_status(status_file)
            self._on_screen[which].observe(status.video, now=now,
                                           playing_for=_how_long_it_has_played(status))
            if status.image:
                watched, fraction = _NOTHING_BEING_WATCHED
            elif status.fraction is None:
                continue
            else:
                watched, fraction = status.video, status.fraction
            for event, video in self._trackers[which].observe(watched, fraction):
                record_watch_event(self.stats_file, video, event)

    def _sample_genau(self, *, now: float) -> None:
        status = read_genau_status(self._genau_status_file)
        self._on_screen[1].observe(status.flick, now=now,
                                   playing_for=_how_long_it_has_been_up(status, now))

    def _sample_main(self, *, now: float) -> None:
        status = read_main_funestra_status(self._main_funestra_status_file)
        self._on_screen[1].observe(status.video, now=now,
                                   playing_for=None if status.image else status.position_ms / 1000)
        if not status.video or status.paused or status.duration_ms <= 0:
            return
        fraction = status.position_ms / status.duration_ms
        for event, video in self._trackers[1].observe(status.video, fraction):
            record_watch_event(self.stats_file, video, event)

    def note_command(self, command: str) -> None:
        for which, nav_commands in NAV_COMMANDS.items():
            if command in nav_commands:
                self._trackers[which].note_user_nav()
                self._he_moved(which)
        discard_which = DISCARD_COMMANDS.get(command)
        if discard_which is not None:
            self._trackers[discard_which].note_discard()
            self._he_moved(discard_which)

    def _he_moved(self, which: int) -> None:
        history = self._on_screen.get(which)
        if history is not None:
            history.a_change_was_asked_for()

    def video_at(self, command: Command, given_at: float | None) -> str:
        if given_at is None:
            return ""
        history = self._on_screen.get(_the_funestra_it_acts_on(command))
        if history is None:
            return ""
        return history.path_at(given_at)


def _the_funestra_it_acts_on(command: Command) -> FunTimeFunestra | None:
    if command.text in VR_MAIN_FUNESTRA_VERBS:
        return FunTimeFunestra.MAIN
    return command.funestra
