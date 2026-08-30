"""Watch tracking: which video each player is on, and what a departure meant.

Every player's current clip is sampled twice a second and fed to the trackers,
which classify playback into completions and skips for the stats file.  The two
satellites also feed a timeline, which is what lets a spoken command be
back-dated to the video that was on screen when the speaker started talking.
"""
from __future__ import annotations

from pathlib import Path

from .command_dispatch import command_side
from .player_status import read_nau_status
from .satellite_control import read_satellite_status
from .video_timeline import VideoTimeline
from .watch_stats import WatchTracker, record_watch_event

# Twice a second: the cadence for sampling every player's current clip (both
# satellites and the main Nau feed).  A satellite video switch is only ever
# bracketed by two samples, so this also bounds how far a back-dated command can
# misplace a switch (the timeline halves it again by dating the switch to the
# bracket's midpoint).  Skipped under OmniPause, where playback is frozen.
SAMPLE_INTERVAL_S = 0.5

# Commands that count as the user navigating away from a video — the signal
# that classifies an early departure as a skip rather than a neutral advance.
# The main player (Nau) navigates with next/prev only; it has no lock/weird/cycle.
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
        nau_status_file: Path,
        satellite_status_files: dict[int, Path],
        stats_file: Path,
    ) -> None:
        self.nau_status_file = nau_status_file
        self.satellite_status_files = satellite_status_files
        self.stats_file = stats_file
        self._trackers: dict[int, WatchTracker] = {1: WatchTracker(), 2: WatchTracker(),
                                                   3: WatchTracker()}
        self._timelines: dict[int, VideoTimeline] = {2: VideoTimeline(), 3: VideoTimeline()}
        self._last_sample = 0.0

    def sample_due(self, *, now: float, paused: bool) -> None:
        """Sample every player, if the cadence says it is time.

        The clock advances whether or not the room is paused, so resuming does
        not fire an off-cadence sample the moment the pause lifts.
        """
        if now - self._last_sample < SAMPLE_INTERVAL_S:
            return
        self._last_sample = now
        if paused:
            return
        self._sample_satellites(now=now)
        self._sample_main()

    def _sample_satellites(self, *, now: float) -> None:
        """Sample each satellite's current video for the trackers and timelines,
        from the status file its native player publishes."""
        for which, status_file in self.satellite_status_files.items():
            status = read_satellite_status(status_file)
            if status.fraction is None:
                continue
            self._timelines[which].observe(status.video, now=now)
            for event, video in self._trackers[which].observe(status.video, status.fraction):
                record_watch_event(self.stats_file, video, event)

    def _sample_main(self) -> None:
        """Sample the main Nau player's current video for watch tracking.

        Nau publishes its playback to the status file; the watched fraction is
        position/duration.  A paused player, one with nothing loaded, or one
        whose duration is not yet known yields no usable sample, so those ticks
        are dropped rather than fed to the tracker.
        """
        status = read_nau_status(self.nau_status_file)
        if not status.video or status.paused or status.duration_ms <= 0:
            return
        fraction = status.position_ms / status.duration_ms
        for event, video in self._trackers[1].observe(status.video, fraction):
            record_watch_event(self.stats_file, video, event)

    def note_command(self, command: str) -> None:
        """Tell the trackers what the user just did to a player, so an early
        departure is classified as a skip (nav) or as nothing at all (a trash,
        whose file is going away and must not be booked against anyone)."""
        for which, nav_commands in NAV_COMMANDS.items():
            if command in nav_commands:
                self._trackers[which].note_user_nav()
        discard_which = DISCARD_COMMANDS.get(command)
        if discard_which is not None:
            self._trackers[discard_which].note_discard()

    def video_at(self, command: str, spoken_at: float | None) -> str:
        """The video *command* was aimed at, or "" for "whatever is playing now".

        A phrase is only recognized once the speaker stops, so a satellite can
        have auto-advanced between "lock…" and "…portrait".  The satellite's
        timeline says which video was on screen when the utterance began — the
        one the speaker was looking at, and therefore meant.  Hotkeys are
        instantaneous and name no video.
        """
        if spoken_at is None:
            return ""
        timeline = self._timelines.get(command_side(command))
        if timeline is None:
            return ""
        return timeline.path_at(spoken_at)
