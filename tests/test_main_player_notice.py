from __future__ import annotations

from player_core.modes import NoticeLevel

from main_player.notice import NoticeWriter


def _read(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )


def test_the_record_it_writes_is_what_fun_time_parses(tmp_path):
    """The whole file as written, because it is a cross-repo contract and
    nothing else in this repo can see it.

    `fun_time.windows_bridge_dispatch_loop.read_main_player_notice` splits each line on
    its first "=" and reads `seq` as a float; the tests around this one parse
    the file the same way, so a reordered, renamed or re-separated record -- or
    a `seq` that stopped being three decimal places -- would keep every one of
    them green while the overlay stopped flashing.
    """
    path = tmp_path / "main_player_notice.txt"

    NoticeWriter(path, clock=lambda: 1234.5).say("no full video", level=NoticeLevel.NOTICE)

    written = path.read_text(encoding="utf-8")
    assert written.splitlines() == [
        "seq=1234.500", "level=notice", "message=no full video"]
    assert written.endswith("\n"), "the last record is terminated like the others"


def test_say_publishes_the_message_and_level(tmp_path):
    path = tmp_path / "state" / "main_player_notice.txt"

    NoticeWriter(path, clock=lambda: 10.0).say("full video not available", level=NoticeLevel.WARNING)

    assert _read(path) == {"seq": "10.000", "level": "warning",
                           "message": "full video not available"}


def test_each_say_advances_the_sequence(tmp_path):
    path = tmp_path / "main_player_notice.txt"
    ticks = iter([10.0, 20.0])
    writer = NoticeWriter(path, clock=lambda: next(ticks))
    writer.say("first", level=NoticeLevel.NOTICE)
    writer.say("second", level=NoticeLevel.NOTICE)

    assert _read(path) == {"seq": "20.000", "level": "notice", "message": "second"}


def test_a_restarted_writer_still_reads_as_newer(tmp_path):
    """A counter restarts at 1 whenever the main player does, so its notices read as older
    than the previous session's and never flashed. A clock stamp cannot."""
    path = tmp_path / "main_player_notice.txt"
    NoticeWriter(path, clock=lambda: 100.0).say("before the restart", level=NoticeLevel.NOTICE)
    NoticeWriter(path, clock=lambda: 101.0).say("after the restart", level=NoticeLevel.NOTICE)

    assert float(_read(path)["seq"]) > 100.0


def test_without_a_path_it_is_inert(tmp_path):
    """Nothing to say to, nothing written, and nothing raised at the caller.

    All eight production callers ignore the answer, so the answer is not the
    contract -- the absence of a file is.
    """
    NoticeWriter(None).say("nothing doing", level=NoticeLevel.NOTICE)

    assert list(tmp_path.iterdir()) == []
