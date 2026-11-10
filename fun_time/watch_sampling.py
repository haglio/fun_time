from __future__ import annotations

from pathlib import Path

from funestra.on_screen_history import OnScreenHistory

from .command_value import Command
from .funestra_status import read_main_funestra_status
from .satellite_control import read_satellite_status
from .watch_stats import WatchTracker, record_watch_event

SAMPLE_INTERVAL_S = 0.5

NAV_COMMANDS: dict[int, frozenset[str]] = {
    1: frozenset({"main_prev", "main_next"}),
    2: frozenset({"portrait_prev", "portrait_next", "portrait_cycle_action", "portrait_cycle_seed"}),
    3: frozenset({"landscape_prev", "landscape_next", "landscape_cycle_action", "landscape_cycle_seed"}),
}

DISCARD_COMMANDS: dict[str, int] = {"portrait_trash": 2, "landscape_trash": 3}

_NOTHING_BEING_WATCHED = ("", 0.0)


class WatchSampler:
    def __init__(
        self,
        *,
        main_funestra_status_file: Path,
        satellite_status_files: dict[int, Path],
        stats_file: Path,
    ) -> None:
        self.main_funestra_status_file = main_funestra_status_file
        self.satellite_status_files = satellite_status_files
        self.stats_file = stats_file
        self._trackers: dict[int, WatchTracker] = {1: WatchTracker(), 2: WatchTracker(),
                                                   3: WatchTracker()}
        self._on_screen: dict[int, OnScreenHistory] = {2: OnScreenHistory(),
                                                       3: OnScreenHistory()}
        self._last_sample = 0.0

    def sample_due(self, *, now: float, omni_paused: bool) -> None:
        if now - self._last_sample < SAMPLE_INTERVAL_S:
            return
        self._last_sample = now
        if omni_paused:
            return
        self._sample_satellites(now=now)
        self._sample_main()

    def _sample_satellites(self, *, now: float) -> None:
        for which, status_file in self.satellite_status_files.items():
            status = read_satellite_status(status_file)
            self._on_screen[which].observe(status.video, now=now)
            if status.image:
                watched, fraction = _NOTHING_BEING_WATCHED
            elif status.fraction is None:
                continue
            else:
                watched, fraction = status.video, status.fraction
            for event, video in self._trackers[which].observe(watched, fraction):
                record_watch_event(self.stats_file, video, event)

    def _sample_main(self) -> None:
        status = read_main_funestra_status(self.main_funestra_status_file)
        if not status.video or status.paused or status.duration_ms <= 0:
            return
        fraction = status.position_ms / status.duration_ms
        for event, video in self._trackers[1].observe(status.video, fraction):
            record_watch_event(self.stats_file, video, event)

    def note_command(self, command: str) -> None:
        for which, nav_commands in NAV_COMMANDS.items():
            if command in nav_commands:
                self._trackers[which].note_user_nav()
        discard_which = DISCARD_COMMANDS.get(command)
        if discard_which is not None:
            self._trackers[discard_which].note_discard()

    def video_at(self, command: Command, spoken_at: float | None) -> str:
        if spoken_at is None:
            return ""
        history = self._on_screen.get(command.funestra)
        if history is None:
            return ""
        return history.path_at(spoken_at)
