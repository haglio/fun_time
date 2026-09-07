"""What the headset is shown of the session's log."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fun_time.event_log import FAVORITE, NOTICE
from fun_time_vr.notices import KEPT, NoticeStrip


def _write(path: Path, message: str, level: int = NOTICE, source: str = "system") -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"ts": 1.0, "level": level, "source": source, "msg": message}) + "\n")


def _log(tmp_path: Path) -> Path:
    path = tmp_path / "event_log.jsonl"
    path.write_text("", encoding="utf-8")
    return path


class TestWhatItPicksUp:
    def test_an_announcement_written_after_it_started_is_held(self, tmp_path):
        path = _log(tmp_path)
        strip = NoticeStrip(path)
        _write(path, "landscape next")

        strip.pump(now=1.0)

        assert [line.message for line in strip.lines] == ["landscape next"]

    def test_the_backlog_it_opened_on_is_not_news(self, tmp_path):
        """The session's history is not what a person is told the moment they
        put the headset on; the strip is for what happens next."""
        path = _log(tmp_path)
        _write(path, "an older command")

        strip = NoticeStrip(path)
        strip.pump(now=1.0)

        assert strip.lines == ()

    def test_the_diagnostic_chatter_under_a_notice_is_not_shown(self, tmp_path):
        """The same bar the desktop's toasts flash on — a notice, or louder —
        so the two surfaces say the same things."""
        path = _log(tmp_path)
        strip = NoticeStrip(path)
        _write(path, "Voice command: landscape_next", level=logging.INFO)
        _write(path, "unrecognized voice command: portrait net", level=logging.ERROR)

        strip.pump(now=1.0)

        assert [line.message for line in strip.lines] == [
            "unrecognized voice command: portrait net"]

    def test_the_level_rides_along_for_the_color(self, tmp_path):
        path = _log(tmp_path)
        strip = NoticeStrip(path)
        _write(path, "clip locked", level=FAVORITE)

        strip.pump(now=1.0)

        assert [line.level for line in strip.lines] == [FAVORITE]


class TestWhatItDrops:
    def test_a_line_fades_after_its_seconds(self, tmp_path):
        path = _log(tmp_path)
        strip = NoticeStrip(path, seconds=5.0)
        _write(path, "landscape next")
        strip.pump(now=100.0)

        strip.pump(now=104.9)
        assert len(strip.lines) == 1

        strip.pump(now=105.1)
        assert strip.lines == ()

    def test_a_line_is_timed_from_when_the_strip_saw_it(self, tmp_path):
        """The writer stamps wall time and the pump runs on a monotonic clock, so
        a strip that faded a line against the record's own timestamp would drop
        it by the difference between the two — which on this machine is days."""
        path = _log(tmp_path)
        strip = NoticeStrip(path, seconds=5.0)
        _write(path, "landscape next")

        strip.pump(now=9_999.0)

        assert len(strip.lines) == 1

    def test_only_the_last_few_are_kept(self, tmp_path):
        path = _log(tmp_path)
        strip = NoticeStrip(path)
        for index in range(KEPT + 3):
            _write(path, f"command {index}")

        strip.pump(now=1.0)

        assert [line.message for line in strip.lines] == [
            f"command {index}" for index in range(3, KEPT + 3)]

    def test_a_missing_log_is_simply_nothing_to_say(self, tmp_path):
        strip = NoticeStrip(tmp_path / "never-written.jsonl")

        strip.pump(now=1.0)

        assert strip.lines == ()
