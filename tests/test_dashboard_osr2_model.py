"""What the Dashboard's OSR2 part reads, and the room it is given: the same on
the desktop and in the headset."""
from __future__ import annotations

from funestra.console_hud import ConsoleHud

from fun_time.dashboard_layout import dashboard_window_height
from fun_time.dashboard_osr2_model import Osr2PartReader, room_for_a_whole_part
from fun_time.osr2_section import osr2_part_path, osr2_readout_path
from fun_time.room_funestras import RoomFunestra
from tests.made_up_osr2_parts import a_part, a_readout, handed_on, published


def test_it_reads_the_part_the_room_publishes_with_its_holders_readout(tmp_path):
    part = published(tmp_path, a_part(RoomFunestra.PORTRAIT))
    readout = handed_on(tmp_path, RoomFunestra.PORTRAIT, a_readout())
    handed_on(tmp_path, RoomFunestra.MAIN, a_readout(speed=90))

    assert Osr2PartReader(tmp_path).read() == ConsoleHud(console=part, drive=readout)


def test_a_part_caught_half_written_leaves_the_last_whole_one(tmp_path):
    reader = Osr2PartReader(tmp_path)
    part = published(tmp_path, a_part())
    reader.read()

    osr2_part_path(tmp_path).write_text('{"main_mode": ', encoding="utf-8")

    assert reader.read() == ConsoleHud(console=part)


def test_a_readout_caught_half_written_leaves_the_last_whole_one(tmp_path):
    reader = Osr2PartReader(tmp_path)
    part = published(tmp_path, a_part())
    readout = handed_on(tmp_path, RoomFunestra.MAIN, a_readout())
    reader.read()

    osr2_readout_path(tmp_path, RoomFunestra.MAIN.label).write_text("speed=5\n", encoding="utf-8")

    assert reader.read() == ConsoleHud(console=part, drive=readout)


def test_a_new_holder_never_wears_the_last_holders_readout(tmp_path):
    reader = Osr2PartReader(tmp_path)
    published(tmp_path, a_part(RoomFunestra.MAIN))
    handed_on(tmp_path, RoomFunestra.MAIN, a_readout())
    reader.read()

    taken = published(tmp_path, a_part(RoomFunestra.LANDSCAPE))

    assert reader.read() == ConsoleHud(console=taken)


def test_until_the_room_publishes_a_part_there_is_nothing_to_draw(tmp_path):
    assert Osr2PartReader(tmp_path).read() is None


def test_the_window_a_session_gives_the_dashboard_holds_a_whole_part():
    assert room_for_a_whole_part()[1] <= dashboard_window_height()
