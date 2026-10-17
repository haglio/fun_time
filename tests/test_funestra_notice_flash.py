from __future__ import annotations

import logging
from pathlib import Path

from fun_time.bridge_records import BridgeConfig
from fun_time.event_log import FAVORITE, NOTICE
from fun_time.role_windows import ChildPids, WindowRoles
from fun_time.windows_bridge_dispatch_loop import DispatchLoopRunner, read_funestra_notice
from tests.role_window_fakes import MAIN_FUNESTRA_PID


def _runner(tmp_path: Path) -> DispatchLoopRunner:
    """A real DispatchLoopRunner whose config's files all live under tmp_path,
    the three Funestras' notice channels among them."""
    path_fields = (
        "portrait_cmd_file", "portrait_paused_file", "portrait_status_file",
        "portrait_playlist_file", "landscape_cmd_file", "landscape_paused_file",
        "landscape_status_file", "landscape_playlist_file", "favs_file",
        "broker_mode_file", "genau_cmd_file", "genau_paused_file",
        "genau_status_file",
        "audio_paused_file", "audio_volume_file", "main_funestra_cmd_file",
        "main_funestra_paused_file", "main_funestra_status_file", "dashboard_state_file",
    )
    config = BridgeConfig(
        **{name: tmp_path / f"{name}.txt" for name in path_fields},
        weird_dir=tmp_path / "weird",
        state_dir=tmp_path,
        main_sources="",
        portrait_sources="",
        landscape_sources="",
        main_funestra_notice_file=tmp_path / "main_player_notice.txt",
        portrait_notice_file=tmp_path / "portrait_notice.txt",
        landscape_notice_file=tmp_path / "landscape_notice.txt",
    )
    return DispatchLoopRunner(
        config=config,
        dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
        shared_state_file=tmp_path / "shared_state.ini",
        ahk_cmd_file=tmp_path / "ahk_cmd.txt",
        windows=WindowRoles(pids=ChildPids(main_funestra=MAIN_FUNESTRA_PID)),
        dashboard_enabled=False,
    )


def _write(path: Path, seq: int, level: str, message: str) -> None:
    path.write_text(f"seq={seq}\nlevel={level}\nmessage={message}\n", encoding="utf-8")


class TestReadFunestraNotice:
    def test_reads_a_published_notice(self, tmp_path):
        path = tmp_path / "main_player_notice.txt"
        _write(path, 3, "warning", "full video not available")

        assert read_funestra_notice(path) == (3.0, "warning", "full video not available")

    def test_missing_file_is_empty(self, tmp_path):
        assert read_funestra_notice(tmp_path / "nope.txt") == (0, "", "")

    def test_malformed_sequence_is_empty(self, tmp_path):
        path = tmp_path / "main_player_notice.txt"
        path.write_text("seq=nonsense\nmessage=hi\n", encoding="utf-8")

        assert read_funestra_notice(path) == (0, "", "")


class TestFlashFunestraNotices:
    """The loop flashes each Funestra's notice exactly once, over that
    Funestra, in the color its kind names."""

    def _loop(self, tmp_path):
        return _runner(tmp_path)  # built before any notice exists: seq latch 0

    def test_flashes_once_then_stays_quiet(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        _write(tmp_path / "main_player_notice.txt", 1, "warning", "full video not available")

        with caplog.at_level(logging.DEBUG):
            loop._flash_funestra_notices()
            first = [r for r in caplog.records if "full video not available" in r.message]
            loop._flash_funestra_notices()
            again = [r for r in caplog.records if "full video not available" in r.message]

        assert len(first) == 1
        assert len(again) == 1  # the repeat tick adds nothing
        assert first[0].levelno == logging.WARNING

    def test_the_funestra_names_the_kind_and_this_side_picks_the_color(self, tmp_path, caplog):
        """A Funestra has no palette.  It says a funscript jump is about a
        funscript, and the level it lands at here is what makes it green — an
        ordinary jump, which says nothing, lands white, and a jump with nowhere
        to go is a warning."""
        loop = self._loop(tmp_path)
        with caplog.at_level(logging.DEBUG):
            _write(tmp_path / "main_player_notice.txt", 1, "favorite", "funscript jump")
            loop._flash_funestra_notices()
            _write(tmp_path / "main_player_notice.txt", 2, "notice", "full video")
            loop._flash_funestra_notices()
            _write(tmp_path / "main_player_notice.txt", 3, "warning", "money shot not available")
            loop._flash_funestra_notices()

        by_message = {r.message: r.levelno for r in caplog.records}
        assert by_message["funscript jump"] == FAVORITE
        assert by_message["full video"] == NOTICE
        assert by_message["money shot not available"] == logging.WARNING

    def test_a_new_sequence_flashes_again(self, tmp_path, caplog):
        loop = self._loop(tmp_path)
        _write(tmp_path / "main_player_notice.txt", 1, "warning", "full video not available")
        with caplog.at_level(logging.DEBUG):
            loop._flash_funestra_notices()
            _write(tmp_path / "main_player_notice.txt", 2, "warning", "money shot not available")
            loop._flash_funestra_notices()

        assert any("money shot not available" in r.message for r in caplog.records)

    def test_a_satellites_notice_flashes_over_that_satellite(self, tmp_path, caplog):
        """Each Funestra has a channel of its own, so a satellite's "no
        funscripting ahead" lands over the satellite that said it and the Main
        Funestra's over the Main, each counted on its own sequence."""
        loop = self._loop(tmp_path)
        _write(tmp_path / "portrait_notice.txt", 1, "warning", "no funscripting ahead")
        _write(tmp_path / "landscape_notice.txt", 1, "favorite", "next funscripted")

        with caplog.at_level(logging.DEBUG):
            loop._flash_funestra_notices()
            loop._flash_funestra_notices()

        assert sorted((r.getMessage(), r.source, r.levelno) for r in caplog.records) == [
            ("next funscripted", "landscape", FAVORITE),
            ("no funscripting ahead", "portrait", logging.WARNING),
        ]


def test_a_notice_from_a_previous_session_does_not_flash_on_open(tmp_path):
    """Opening Fun Time replayed whatever was last in the file, so a stale
    'full video not available' appeared the instant it started.  The latch is
    the real constructor's own: the runner is built AFTER the stale notice
    landed, exactly as a fresh session opens over last session's file."""
    _write(tmp_path / "main_player_notice.txt", 500, "warning", "stale from last time")
    _write(tmp_path / "portrait_notice.txt", 500, "warning", "stale on the side")

    loop = _runner(tmp_path)

    seen: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    logger = logging.getLogger("fun_time.windows_bridge_dispatch_loop")
    logger.addHandler(handler)
    try:
        loop._flash_funestra_notices()
    finally:
        logger.removeHandler(handler)

    assert not [m for m in seen if "stale" in m]
