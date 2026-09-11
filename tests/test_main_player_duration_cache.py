from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from main_player.duration_cache import DurationCache, ffprobe_duration


class FakeProber:
    """Records probe calls and returns canned durations."""

    def __init__(self, durations: dict[Path, float]) -> None:
        self._durations = durations
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> float:
        self.calls.append(path)
        return self._durations[path]


def _make_video(path: Path, content: str = "fake") -> Path:
    path.write_text(content)
    return path


class TestDurationCache:
    def test_probes_and_returns_duration(self, tmp_path):
        vid = _make_video(tmp_path / "a.mp4")
        prober = FakeProber({vid: 123.0})
        cache = DurationCache(tmp_path / "dur.json", prober=prober)

        assert cache.duration_for(vid) == 123.0
        assert prober.calls == [vid]

    def test_second_call_uses_cache_without_reprobing(self, tmp_path):
        vid = _make_video(tmp_path / "a.mp4")
        prober = FakeProber({vid: 45.0})
        cache = DurationCache(tmp_path / "dur.json", prober=prober)

        cache.duration_for(vid)
        cache.duration_for(vid)

        assert prober.calls == [vid]  # probed once

    def test_size_change_invalidates(self, tmp_path):
        vid = _make_video(tmp_path / "a.mp4", content="short")
        prober = FakeProber({vid: 10.0})
        cache = DurationCache(tmp_path / "dur.json", prober=prober)
        cache.duration_for(vid)

        vid.write_text("a much longer body than before")  # size changes
        prober._durations[vid] = 20.0
        assert cache.duration_for(vid) == 20.0
        assert prober.calls == [vid, vid]  # re-probed

    def test_mtime_change_invalidates(self, tmp_path):
        vid = _make_video(tmp_path / "a.mp4")
        prober = FakeProber({vid: 10.0})
        cache = DurationCache(tmp_path / "dur.json", prober=prober)
        cache.duration_for(vid)

        # Same size, newer mtime -> must re-probe.
        st = vid.stat()
        os.utime(vid, (st.st_atime, st.st_mtime + 1000))
        prober._durations[vid] = 33.0
        assert cache.duration_for(vid) == 33.0
        assert prober.calls == [vid, vid]

    def test_persists_and_reloads_across_instances(self, tmp_path):
        vid = _make_video(tmp_path / "a.mp4")
        cache_path = tmp_path / "dur.json"
        prober = FakeProber({vid: 77.0})
        cache = DurationCache(cache_path, prober=prober)
        cache.duration_for(vid)
        cache.save()

        # A fresh instance reads the file and must not re-probe.
        prober2 = FakeProber({vid: 77.0})
        reloaded = DurationCache(cache_path, prober=prober2)
        assert reloaded.duration_for(vid) == 77.0
        assert prober2.calls == []

    def test_save_writes_expected_json(self, tmp_path):
        vid = _make_video(tmp_path / "a.mp4")
        cache_path = tmp_path / "dur.json"
        cache = DurationCache(cache_path, prober=FakeProber({vid: 5.0}))
        cache.duration_for(vid)
        cache.save()

        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert data[str(vid)]["duration_s"] == 5.0
        assert data[str(vid)]["size"] == vid.stat().st_size

    def test_missing_file_probes_without_caching(self, tmp_path):
        missing = tmp_path / "gone.mp4"
        prober = FakeProber({missing: 9.0})
        cache = DurationCache(tmp_path / "dur.json", prober=prober)

        assert cache.duration_for(missing) == 9.0
        # Nothing to persist for an unstatable path.
        cache.save()
        assert not (tmp_path / "dur.json").exists()


class TestWhatItDoesWhenSomethingIsUnreadable:
    """Three swallows, each with a documented answer that nothing checked.

    Coverage listed every `except` body here as missing, and a swallow nobody
    exercises is a swallow that can become a raise without the suite noticing --
    which for a library scan means startup dying on one bad file.
    """

    def test_a_probe_that_fails_reads_as_no_duration(self, monkeypatch):
        """Length is what the modes filter on, so an unprobeable video falls out
        of both of them rather than taking the scan down."""
        def refuse(*_args, **_kwargs):
            raise subprocess.CalledProcessError(1, "ffprobe")
        monkeypatch.setattr(subprocess, "check_output", refuse)

        assert ffprobe_duration(Path("C:/example/library/videos/gamma reel.mp4")) == 0.0

    def test_a_probe_that_answers_nonsense_reads_as_no_duration(self, monkeypatch):
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "not a number")

        assert ffprobe_duration(Path("C:/example/library/videos/gamma reel.mp4")) == 0.0

    def test_a_cache_file_it_cannot_read_starts_empty(self, tmp_path):
        """A truncated or hand-edited cache is a slow first scan, not a crash."""
        cache_path = tmp_path / "dur.json"
        cache_path.write_text("{not json", encoding="utf-8")
        vid = _make_video(tmp_path / "a.mp4")

        cache = DurationCache(cache_path, prober=FakeProber({vid: 7.0}))

        assert cache.duration_for(vid) == 7.0  # probed, not read back
