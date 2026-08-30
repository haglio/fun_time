"""Watch tracking: what each player is on, and how a departure is classified.

Driven directly, without a dispatch loop around it — the sampler's whole input
is three status files and a stats file, so nothing else needs to exist for it
to be asked what it recorded.
"""
from __future__ import annotations

from pathlib import Path

from fun_time.media_metadata import normalize_path_key
from fun_time.watch_sampling import WatchSampler
from fun_time.watch_stats import load_watch_stats


def make_sampler(tmp_path: Path) -> WatchSampler:
    return WatchSampler(
        nau_status_file=tmp_path / "nau_status.txt",
        satellite_status_files={2: tmp_path / "portrait_status.txt",
                                3: tmp_path / "landscape_status.txt"},
        stats_file=tmp_path / "watch_stats.json",
    )


def _publish(path: Path, video, *, fraction: float) -> None:
    """A satellite player's status file, the way its own writer publishes it."""
    path.write_text(
        f"video={video}\nposition_ms={round(fraction * 1000)}\nduration_ms=1000\n"
        "paused=0\nlocked=0\n",
        encoding="utf-8",
    )


def test_a_video_watched_to_the_end_is_recorded_as_a_completion(tmp_path):
    video = tmp_path / "alpha.mp4"
    video.write_text("x", encoding="utf-8")
    sampler = make_sampler(tmp_path)

    _publish(tmp_path / "portrait_status.txt", video, fraction=0.1)
    sampler.sample_due(now=100.0, paused=False)
    _publish(tmp_path / "portrait_status.txt", video, fraction=0.9)
    sampler.sample_due(now=101.1, paused=False)
    _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
    sampler.sample_due(now=102.2, paused=False)

    stats = load_watch_stats(tmp_path / "watch_stats.json")
    assert stats[normalize_path_key(str(video))]["completions"] == 1


def _publish_nau(path: Path, video, *, position_ms: int, duration_ms: int,
                 paused: bool = False) -> None:
    """Nau's status file, the way nau/status.py writes it."""
    path.write_text(
        f"video={video}\nposition_ms={position_ms}\nduration_ms={duration_ms}\n"
        f"state=normal\npaused={'1' if paused else '0'}\n",
        encoding="utf-8",
    )


def _make_video(tmp_path: Path, name: str) -> Path:
    """A real file on disk — record_watch_event prunes keys that don't exist."""
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    return path


class TestSamplingCadence:
    def test_a_sample_inside_the_interval_is_not_taken_again(self, tmp_path):
        sampler = make_sampler(tmp_path)
        video = _make_video(tmp_path, "alpha.mp4")

        _publish(tmp_path / "portrait_status.txt", video, fraction=0.9)
        sampler.sample_due(now=100.0, paused=False)
        _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
        sampler.sample_due(now=100.1, paused=False)

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}

    def test_a_paused_room_is_not_sampled_at_all(self, tmp_path):
        """OmniPause freezes playback, so a position held near the end under it
        must not later read as a video watched to the end."""
        sampler = make_sampler(tmp_path)
        video = _make_video(tmp_path, "alpha.mp4")

        _publish(tmp_path / "portrait_status.txt", video, fraction=0.9)
        sampler.sample_due(now=100.0, paused=True)
        _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
        sampler.sample_due(now=101.0, paused=False)

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}


class TestTheMainPlayer:
    """Nau's status feed is watch-tracked just like a satellite's."""

    def _sample(self, sampler, at: float) -> None:
        sampler.sample_due(now=at, paused=False)

    def test_a_nau_video_watched_to_the_end_then_departed_is_a_completion(self, tmp_path):
        sampler = make_sampler(tmp_path)
        watched = _make_video(tmp_path, "watched.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _publish_nau(status, watched, position_ms=9000, duration_ms=10000)
        self._sample(sampler, 100.0)
        _publish_nau(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(watched))]["completions"] == 1

    def test_an_unknown_duration_yields_no_sample(self, tmp_path):
        """Before Nau knows the clip length it publishes duration_ms=0; no
        fraction can be formed, so the sample is dropped (never a divide-by-zero)."""
        sampler = make_sampler(tmp_path)
        early = _make_video(tmp_path, "early.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _publish_nau(status, early, position_ms=5000, duration_ms=0)
        self._sample(sampler, 100.0)
        _publish_nau(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        assert normalize_path_key(str(early)) not in load_watch_stats(tmp_path / "watch_stats.json")

    def test_a_paused_nau_is_not_watching(self, tmp_path):
        sampler = make_sampler(tmp_path)
        watched = _make_video(tmp_path, "watched.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _publish_nau(status, watched, position_ms=9000, duration_ms=10000, paused=True)
        self._sample(sampler, 100.0)
        _publish_nau(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        assert normalize_path_key(str(watched)) not in load_watch_stats(tmp_path / "watch_stats.json")

    def test_the_blank_between_videos_is_not_a_departure(self, tmp_path):
        """Between videos Nau can briefly publish an empty video path; that blank
        must not read as the watched video departing (a spurious completion)."""
        sampler = make_sampler(tmp_path)
        watched = _make_video(tmp_path, "watched.mp4")
        status = tmp_path / "nau_status.txt"

        _publish_nau(status, watched, position_ms=9000, duration_ms=10000)
        self._sample(sampler, 100.0)
        _publish_nau(status, "", position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        assert normalize_path_key(str(watched)) not in load_watch_stats(tmp_path / "watch_stats.json")

    def test_next_marks_the_departed_nau_video_as_a_skip(self, tmp_path):
        """Pressing next on the main player is the "user nav" signal: a Nau video
        left early right after a next counts as a skip, like a satellite next."""
        sampler = make_sampler(tmp_path)
        early = _make_video(tmp_path, "early.mp4")
        nextv = _make_video(tmp_path, "next.mp4")
        status = tmp_path / "nau_status.txt"

        _publish_nau(status, early, position_ms=1000, duration_ms=10000)
        self._sample(sampler, 100.0)
        sampler.note_command("main_next")
        _publish_nau(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(early))]["skips"] == 1

    def test_a_trashed_video_is_booked_against_nobody(self, tmp_path):
        """The file is going away; classifying its departure would credit or
        penalize a clip the user threw out."""
        sampler = make_sampler(tmp_path)
        alpha = _make_video(tmp_path, "alpha.mp4")

        _publish(tmp_path / "portrait_status.txt", alpha, fraction=0.2)
        sampler.sample_due(now=100.0, paused=False)
        sampler.note_command("portrait_trash")
        _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
        sampler.sample_due(now=101.0, paused=False)

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}


class TestBackDating:
    """A phrase is only recognized once the speaker stops, so a satellite can
    have auto-advanced between "lock…" and "…portrait"; the timeline says which
    video was on screen when the utterance began."""

    def _observe(self, sampler, side: str, video: str, *, at: float) -> None:
        _publish(sampler.satellite_status_files[2 if side == "portrait" else 3],
                 video, fraction=0.1)
        sampler.sample_due(now=at, paused=False)

    def test_a_spoken_command_names_the_video_the_speaker_was_looking_at(self, tmp_path):
        sampler = make_sampler(tmp_path)
        self._observe(sampler, "portrait", "C:\\clips\\meant.mp4", at=100.0)
        self._observe(sampler, "portrait", "C:\\clips\\advanced_to.mp4", at=101.0)

        assert sampler.video_at("portrait_lock", 100.2) == "C:\\clips\\meant.mp4"

    def test_each_satellite_reads_its_own_timeline(self, tmp_path):
        """A landscape command must not be back-dated against the portrait's
        videos."""
        sampler = make_sampler(tmp_path)
        self._observe(sampler, "portrait", "C:\\clips\\portrait.mp4", at=100.0)
        self._observe(sampler, "landscape", "C:\\clips\\landscape.mp4", at=100.6)

        assert sampler.video_at("landscape_trash", 100.7) == "C:\\clips\\landscape.mp4"

    def test_a_hotkey_command_names_no_video(self, tmp_path):
        """A keypress is instantaneous: it means whatever is playing right now."""
        sampler = make_sampler(tmp_path)
        self._observe(sampler, "portrait", "C:\\clips\\meant.mp4", at=100.0)

        assert sampler.video_at("portrait_trash", None) == ""
