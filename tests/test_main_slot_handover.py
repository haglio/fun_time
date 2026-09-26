from __future__ import annotations

import logging
from pathlib import Path

from player_core.modes import MainMode

from fun_time.main_slot_handover import WAIT_FOR_GENAU_TO_TURN_SOLID_S, MainSlotHandover
from fun_time.role_windows import MAIN_BLANK_SETTLE_S
from tests.role_window_fakes import FakeClock


class _MainSlotWindows:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.done: list[tuple] = []

    def hide_after_settle(self, role: str) -> None:
        self.done.append(("hide_after_settle", role))

    def restack_main_slot(self, main_mode: MainMode, *, paused: bool = False) -> None:
        self.done.append(("restack_main_slot", main_mode, paused))


def _handover(tmp_path: Path) -> tuple[MainSlotHandover, _MainSlotWindows]:
    windows = _MainSlotWindows()
    return MainSlotHandover(
        windows=windows,
        genau_cmd_file=tmp_path / "genau_cmd.txt",
        main_player_cmd_file=tmp_path / "main_player_cmd.txt",
        genau_status_file=tmp_path / "genau_status.txt",
    ), windows


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _genau_says(tmp_path: Path, *, hud: bool) -> None:
    (tmp_path / "genau_status.txt").write_text(f"hud={int(hud)}\n", encoding="utf-8")


def test_genau_turns_into_the_hud_only_once_the_main_player_has_settled_under_it(tmp_path):
    handover, windows = _handover(tmp_path)

    handover.begin(MainMode.VIDEO)
    handover.sync(MainMode.VIDEO, paused=False)
    assert _lines(tmp_path / "genau_cmd.txt") == []

    windows.clock.advance(MAIN_BLANK_SETTLE_S)
    handover.sync(MainMode.VIDEO, paused=False)
    handover.sync(MainMode.VIDEO, paused=False)
    assert _lines(tmp_path / "genau_cmd.txt") == ["HUD_ON"]


def test_a_switch_back_to_genau_before_the_settle_keeps_genau_the_display(tmp_path):
    handover, windows = _handover(tmp_path)

    handover.begin(MainMode.VIDEO)
    windows.clock.advance(MAIN_BLANK_SETTLE_S)
    handover.sync(MainMode.GENAU, paused=False)

    assert _lines(tmp_path / "genau_cmd.txt") == []


def test_the_main_player_stays_under_genau_until_genau_says_it_is_solid(tmp_path):
    handover, windows = _handover(tmp_path)
    _genau_says(tmp_path, hud=True)

    handover.begin(MainMode.GENAU)
    handover.sync(MainMode.GENAU, paused=False)
    assert (_lines(tmp_path / "main_player_cmd.txt"), windows.done) == ([], [])

    _genau_says(tmp_path, hud=False)
    handover.sync(MainMode.GENAU, paused=False)
    handover.sync(MainMode.GENAU, paused=False)
    assert _lines(tmp_path / "main_player_cmd.txt") == ["DISPLAY_OFF"]
    assert windows.done == [("hide_after_settle", "main_player"),
                            ("restack_main_slot", MainMode.GENAU, False)]


def test_a_genau_that_never_says_it_is_solid_gets_the_main_player_out_of_the_way_all_the_same(
        tmp_path):
    handover, windows = _handover(tmp_path)

    handover.begin(MainMode.GENAU)
    handover.sync(MainMode.GENAU, paused=True)
    assert windows.done == []

    windows.clock.advance(WAIT_FOR_GENAU_TO_TURN_SOLID_S)
    handover.sync(MainMode.GENAU, paused=True)
    assert _lines(tmp_path / "main_player_cmd.txt") == ["DISPLAY_OFF"]
    assert windows.done == [("hide_after_settle", "main_player"),
                            ("restack_main_slot", MainMode.GENAU, True)]


def test_a_switch_back_to_video_before_genau_is_solid_leaves_the_main_player_as_it_is(tmp_path):
    handover, windows = _handover(tmp_path)

    handover.begin(MainMode.GENAU)
    handover.sync(MainMode.VIDEO, paused=False)
    _genau_says(tmp_path, hud=False)
    handover.sync(MainMode.GENAU, paused=False)

    assert (_lines(tmp_path / "main_player_cmd.txt"), windows.done) == ([], [])


def test_a_main_player_that_steps_aside_without_hearing_from_genau_says_so_in_the_log(
        tmp_path, caplog):
    handover, windows = _handover(tmp_path)

    handover.begin(MainMode.GENAU)
    windows.clock.advance(WAIT_FOR_GENAU_TO_TURN_SOLID_S)
    with caplog.at_level(logging.WARNING, logger="fun_time.main_slot_handover"):
        handover.sync(MainMode.GENAU, paused=False)

    assert "Genau did not report turning solid" in caplog.text
