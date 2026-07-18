"""Recent playback history for one player, so a late command can be back-dated.

Speech recognition only finalizes a phrase once the speaker stops, so a spoken
"lock portrait" reaches the dispatcher a second or two after the user began
saying it — by which time an auto-advancing satellite may already be on the next
video.  Sampling each player's current video into a timeline lets the dispatcher
resolve the video that was on screen when the utterance *started*, which is the
one the user meant.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimelineEntry:
    path: str
    started_at: float


class VideoTimeline:
    """A short (path, started-at) history built from periodic samples."""

    def __init__(self, *, history_s: float = 30.0) -> None:
        self.history_s = history_s
        self._entries: list[TimelineEntry] = []
        self._last_seen_at = 0.0

    def observe(self, path: str, *, now: float) -> None:
        """Record that *path* is the current video as of *now*.

        A switch is only ever bounded by the two samples that straddle it, so it
        is dated to their midpoint: the residual error is then half a sample
        interval in either direction rather than a full interval in one.
        """
        if not path:
            # A player's status file carries no path before it has loaded
            # anything — a gap in the record, not a switch to a new video.
            return
        if self._entries and self._entries[-1].path == path:
            self._last_seen_at = now
            return
        started_at = (self._last_seen_at + now) / 2 if self._entries else now
        self._entries.append(TimelineEntry(path, started_at))
        self._last_seen_at = now
        self._prune(now - self.history_s)

    def _prune(self, cutoff: float) -> None:
        """Drop entries the window no longer needs.

        The newest entry that started at or before *cutoff* is what a query at
        the window's edge must still resolve to, so it is kept along with
        everything after it.
        """
        keep_from = 0
        for index, entry in enumerate(self._entries):
            if entry.started_at <= cutoff:
                keep_from = index
        del self._entries[:keep_from]

    def path_at(self, when: float) -> str:
        """The video playing at *when*, or "" when the history doesn't reach it."""
        for entry in reversed(self._entries):
            if entry.started_at <= when:
                return entry.path
        return ""
