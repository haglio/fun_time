from __future__ import annotations

import logging
from pathlib import Path

from fun_time.bridge_records import (
    FAILED_NOTICE_LEVEL,
    FAVORITE_NOTICE_LEVEL,
    BridgeConfig,
)
from fun_time.event_log import NOTICE
from fun_time.role_windows import ChildPids, WindowRoles
from fun_time.windows_bridge_dispatch_loop import DispatchLoopRunner, read_nau_notice
from tests.role_window_fakes import NAU_PID


def _runner(tmp_path: Path) -> DispatchLoopRunner:
    """A real DispatchLoopRunner whose config's files all live under tmp_path.

    The flash tests used to graft _flash_nau_notice onto a two-attribute
    stand-in, which welded them to the method's exact name and to which
    state it happens to read off self — and had to fake by hand the
    stale-notice latch the real constructor owns.
    """
    path_fields = (
        "portrait_cmd_file", "portrait_paused_file", "portrait_status_file",
        "portrait_playlist_file", "landscape_cmd_file", "landscape_paused_file",
        "landscape_status_file", "landscape_playlist_file", "favs_file",
        "genau_mode_file", "genau_cmd_file", "genau_paused_file",
        "audio_paused_file", "audio_volume_file", "nau_cmd_file",
        "nau_paused_file", "nau_status_file", "dashboard_state_file",
    )
    config = BridgeConfig(
        **{name: tmp_path / f"{name}.txt" for name in path_fields},
        weird_dir=tmp_path / "weird",
        state_dir=tmp_path,
        main_sources="",
        portrait_sources="",
        landscape_sources="",
        nau_notice_file=tmp_path / "nau_notice.txt",
    )
    return DispatchLoopRunner(
        config=config,
        dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
        shared_state_file=tmp_path / "shared_state.ini",
        ahk_cmd_file=tmp_path / "ahk_cmd.txt",
        windows=WindowRoles(pids=ChildPids(nau=NAU_PID)),
        dashboard_enabled=False,
    )


def _write(path: Path, seq: int, level: str, message: str) -> None:
    path.write_text(f"seq={seq}\nlevel={level}\nmessage={message}\n", encoding="utf-8")


class TestReadNauNotice:
    def test_reads_a_published_notice(self, tmp_path):
        path = tmp_path / "nau_notice.txt"
        _write(path, 3, "error", "full video not available")

        assert read_nau_notice(path) == (3.0, "error", "full video not available")

    def test_missing_file_is_empty(self, tmp_path):
        assert read_nau_notice(tmp_path / "nope.txt") == (0, "", "")

    def test_malformed_sequence_is_empty(self, tmp_path):
        path = tmp_path / "nau_notice.txt"
        path.write_text("seq=nonsense\nmessage=hi\n", encoding="utf-8")

        assert read_nau_notice(path) == (0, "", "")


class TestFlashNauNotice:
    """The loop flashes each notice exactly once, red for an error level."""

    def _loop(self, tmp_path):
        return _runner(tmp_path)  # built before any notice exists: seq latch 0

    def test_flashes_once_then_stays_quiet(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        _write(tmp_path / "nau_notice.txt", 1, "error", "full video not available")

        with caplog.at_level(logging.DEBUG):
            loop._flash_nau_notice()
            first = [r for r in caplog.records if "full video not available" in r.message]
            loop._flash_nau_notice()
            again = [r for r in caplog.records if "full video not available" in r.message]

        assert len(first) == 1
        assert len(again) == 1  # the repeat tick adds nothing
        assert first[0].levelno == FAILED_NOTICE_LEVEL

    def test_nau_names_the_kind_and_this_side_picks_the_color(self, tmp_path, caplog):
        """Nau has no palette.  It says a funscript jump is about a funscript, and
        the level it lands at here is what makes it green — an ordinary jump, which
        says nothing, lands white."""
        loop = self._loop(tmp_path)
        with caplog.at_level(logging.DEBUG):
            _write(tmp_path / "nau_notice.txt", 1, "favorite", "funscript jump")
            loop._flash_nau_notice()
            _write(tmp_path / "nau_notice.txt", 2, "notice", "full video")
            loop._flash_nau_notice()

        by_message = {r.message: r.levelno for r in caplog.records}
        assert by_message["funscript jump"] == FAVORITE_NOTICE_LEVEL
        assert by_message["full video"] == NOTICE

    def test_a_new_sequence_flashes_again(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        _write(tmp_path / "nau_notice.txt", 1, "error", "full video not available")
        with caplog.at_level(logging.DEBUG):
            loop._flash_nau_notice()
            _write(tmp_path / "nau_notice.txt", 2, "error", "money shot not available")
            loop._flash_nau_notice()

        assert any("money shot not available" in r.message for r in caplog.records)


def test_a_notice_from_a_previous_session_does_not_flash_on_open(tmp_path):
    """Opening Fun Time replayed whatever was last in the file, so a stale
    'full video not available' appeared the instant it started.  The latch is
    the real constructor's own: the runner is built AFTER the stale notice
    landed, exactly as a fresh session opens over last session's file."""
    path = tmp_path / "nau_notice.txt"
    _write(path, 500, "error", "stale from last time")

    loop = _runner(tmp_path)

    seen: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    logger = logging.getLogger("fun_time.windows_bridge_dispatch_loop")
    logger.addHandler(handler)
    try:
        loop._flash_nau_notice()
    finally:
        logger.removeHandler(handler)

    assert not [m for m in seen if "stale from last time" in m]
