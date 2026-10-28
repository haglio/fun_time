"""The OSR2 part at the Dashboard's right end: what it reads, and what it draws.
What it reads, file by file, is tests/test_dashboard_osr2_model.py's."""
from __future__ import annotations

import pytest
from funestra.console import ConsoleModel
from funestra.console_hud import ConsoleHud
from funestra.device_panel import DevicePanel
from funestra.drive_readout import DriveHud

from fun_time.dashboard_osr2 import Osr2PartWidget
from fun_time.room_funestras import RoomFunestra
from tests.made_up_osr2_parts import a_part, a_readout, handed_on, published


def _drawn(part: ConsoleModel, readout: DriveHud | None = None):
    return DevicePanel(post=[].append).picture(ConsoleHud(console=part, drive=readout))


@pytest.fixture
def widget(tmp_path):
    part = Osr2PartWidget(tmp_path)
    try:
        yield part
    finally:
        part.shutdown()
        part.close()


def test_it_draws_the_part_the_room_publishes_with_its_holders_readout(widget, tmp_path):
    part = published(tmp_path, a_part(RoomFunestra.PORTRAIT))
    readout = handed_on(tmp_path, RoomFunestra.PORTRAIT, a_readout())
    handed_on(tmp_path, RoomFunestra.MAIN, a_readout(speed=90))

    widget.poll()

    assert widget.picture() == _drawn(part, readout)


def test_until_the_room_publishes_a_part_it_draws_nothing(widget):
    widget.poll()
    raw, _size = widget.picture()

    assert set(raw[3::4]) == {0}


def test_every_read_repaints_it_once_a_part_is_up_for_the_dot_walks_by_the_clock(
        widget, tmp_path, monkeypatch):
    repaints = []
    monkeypatch.setattr(widget, "update", lambda: repaints.append("repaint"))

    widget.poll()
    published(tmp_path, a_part())
    widget.poll()
    widget.poll()

    assert repaints == ["repaint"] * 2


def test_it_holds_the_room_a_whole_part_takes_before_one_has_arrived(widget, tmp_path):
    reserved = widget.size()

    published(tmp_path, a_part())
    handed_on(tmp_path, RoomFunestra.MAIN, a_readout())
    widget.poll()
    widget.grab()

    assert widget.size() == reserved
    assert (reserved.width(), reserved.height()) == _drawn(a_part(), a_readout())[1]


def test_it_reads_the_part_by_itself_ten_times_a_second(widget, tmp_path):
    part = published(tmp_path, a_part())

    widget._timer.timeout.emit()

    assert (widget._timer.isActive(), widget._timer.interval()) == (True, 100)
    assert widget.picture() == _drawn(part)


def test_once_shut_down_it_reads_nothing_more(widget):
    widget.shutdown()

    assert not widget._timer.isActive()
