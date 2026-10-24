"""Watch tracking: which video each Funestra is on, and what a departure meant.

Every Funestra's current clip is sampled twice a second and fed to the trackers,
which classify playback into completions and skips for the stats file.  The two
satellites also feed a timeline, which is what lets a spoken command be
back-dated to the video that was on screen when the speaker started talking.
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from .command_dispatch import command_funestra
from .funestra_status import read_main_funestra_status
from .satellite_control import read_satellite_status
from .video_timeline import VideoTimeline
from .watch_stats import WatchTracker, record_watch_event

# Twice a second: the cadence for sampling every Funestra's current clip (both
# satellites and the Main Funestra feed).  A satellite video switch is only ever
# bracketed by two samples, so this also bounds how far a back-dated command can
# misplace a switch (the timeline halves it again by dating the switch to the
# bracket's midpoint).  Skipped under OmniPause, where playback is frozen.
SAMPLE_INTERVAL_S = 0.5

# Commands that count as the user navigating away from a video — the signal
# that classifies an early departure as a skip rather than a neutral advance.
# The Main Funestra navigates with next/prev only; it has no lock/weird/cycle.
NAV_COMMANDS: dict[int, frozenset[str]] = {
    1: frozenset({"main_prev", "main_next"}),
    2: frozenset({"portrait_prev", "portrait_next", "portrait_cycle_action", "portrait_cycle_seed"}),
    3: frozenset({"landscape_prev", "landscape_next", "landscape_cycle_action", "landscape_cycle_seed"}),
}

DISCARD_COMMANDS: dict[str, int] = {"portrait_trash": 2, "landscape_trash": 3}


class WatchSampler:
    """The trackers, the timelines, and the files they are fed from."""

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
        self._timelines: dict[int, VideoTimeline] = {2: VideoTimeline(), 3: VideoTimeline()}
        self._last_sample = 0.0

    def sample_due(self, *, now: float, paused: bool, hosted: Collection[int] = ()) -> None:
        """Sample every Funestra, if the cadence says it is time.

        The clock advances whether or not the room is paused, so resuming does
        not fire an off-cadence sample the moment the pause lifts.  The
        satellites *hosted* book nothing: in their Origenerator mode they play
        the hosted app's pictures, which are no library videos to book a watch
        against.
        """
        if now - self._last_sample < SAMPLE_INTERVAL_S:
            return
        self._last_sample = now
        if paused:
            return
        self._sample_satellites(now=now, hosted=hosted)
        self._sample_main()

    def _sample_satellites(self, *, now: float, hosted: Collection[int]) -> None:
        for which, status_file in self.satellite_status_files.items():
            status = read_satellite_status(status_file)
            self._timelines[which].observe(status.video, now=now)
            if which in hosted or status.fraction is None:
                continue
            for event, video in self._trackers[which].observe(status.video, status.fraction):
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

    def video_at(self, command: str, spoken_at: float | None) -> str:
        if spoken_at is None:
            return ""
        timeline = self._timelines.get(command_funestra(command))
        if timeline is None:
            return ""
        return timeline.path_at(spoken_at)
