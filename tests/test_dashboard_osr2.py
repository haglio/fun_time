"""The OSR2 part at the Dashboard's right end: what it reads, and what it draws."""
from __future__ import annotations

import pytest
from funestra.console import OSR2_DRIVING, ConsoleModel, console_text, read_console
from funestra.console_hud import ConsoleHud
from funestra.device_panel import DevicePanel
from funestra.drive_readout import DriveHud, publish_drive, read_drive
from funestra.modes import MainMode, Osr2State

from fun_time.dashboard_layout import dashboard_window_height
from fun_time.dashboard_osr2 import Osr2PartWidget, room_for_a_whole_part
from fun_time.osr2_section import osr2_part, osr2_part_path, osr2_readout_path
from fun_time.room_funestras import RoomFunestra


def _part(holder: RoomFunestra = RoomFunestra.MAIN, *, broker: bool = True) -> ConsoleModel:
    return osr2_part(holder, main_mode=MainMode.KINO, osr2=Osr2State.ROBOT_HAND,
                     control=OSR2_DRIVING, paused=False, learned=False, shape="sine",
                     broker=broker, max_intensity=100)


def _readout(speed: int = 50) -> DriveHud:
    return DriveHud(speed=speed, amplitude=60, center=50, waveform=tuple(0.5 for _ in range(80)))


def _publish(state_dir, part: ConsoleModel) -> ConsoleModel:
    path = osr2_part_path(state_dir)
    path.write_text(console_text(part), encoding="utf-8")
    return read_console(path)


def _hand_on(state_dir, holder: RoomFunestra, readout: DriveHud) -> DriveHud:
    path = osr2_readout_path(state_dir, holder.label)
    publish_drive(path, readout)
    return read_drive(path)


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
    part = _publish(tmp_path, _part(RoomFunestra.PORTRAIT))
    readout = _hand_on(tmp_path, RoomFunestra.PORTRAIT, _readout())
    _hand_on(tmp_path, RoomFunestra.MAIN, _readout(speed=90))

    widget.poll()

    assert widget.picture() == _drawn(part, readout)


def test_a_part_caught_half_written_leaves_the_last_whole_one_drawn(widget, tmp_path):
    part = _publish(tmp_path, _part())
    widget.poll()

    osr2_part_path(tmp_path).write_text('{"main_mode": ', encoding="utf-8")
    widget.poll()

    assert widget.picture() == _drawn(part)


def test_a_readout_caught_half_written_leaves_the_last_whole_one_drawn(widget, tmp_path):
    part = _publish(tmp_path, _part())
    readout = _hand_on(tmp_path, RoomFunestra.MAIN, _readout())
    widget.poll()

    osr2_readout_path(tmp_path, RoomFunestra.MAIN.label).write_text("speed=5\n", encoding="utf-8")
    widget.poll()

    assert widget.picture() == _drawn(part, readout)


def test_a_new_holder_never_wears_the_last_holders_readout(widget, tmp_path):
    _publish(tmp_path, _part(RoomFunestra.MAIN))
    _hand_on(tmp_path, RoomFunestra.MAIN, _readout())
    widget.poll()

    taken = _publish(tmp_path, _part(RoomFunestra.LANDSCAPE))
    widget.poll()

    assert widget.picture() == _drawn(taken)


def test_until_the_room_publishes_a_part_it_draws_nothing(widget):
    widget.poll()
    raw, _size = widget.picture()

    assert set(raw[3::4]) == {0}


def test_it_repaints_only_when_the_part_or_the_readout_has_changed(widget, tmp_path, monkeypatch):
    repaints = []
    monkeypatch.setattr(widget, "update", lambda: repaints.append("repaint"))
    _publish(tmp_path, _part())

    widget.poll()
    widget.poll()
    _hand_on(tmp_path, RoomFunestra.MAIN, _readout())
    widget.poll()
    _publish(tmp_path, _part())
    widget.poll()
    _publish(tmp_path, _part(broker=False))
    widget.poll()

    assert repaints == ["repaint"] * 3


def test_it_holds_the_room_a_whole_part_takes_before_one_has_arrived(widget, tmp_path):
    reserved = widget.size()

    _publish(tmp_path, _part())
    _hand_on(tmp_path, RoomFunestra.MAIN, _readout())
    widget.poll()
    widget.grab()

    assert widget.size() == reserved
    assert (reserved.width(), reserved.height()) == _drawn(_part(), _readout())[1]


def test_the_window_a_session_gives_the_dashboard_holds_a_whole_part():
    assert room_for_a_whole_part()[1] <= dashboard_window_height()


def test_it_reads_the_part_by_itself_ten_times_a_second(widget, tmp_path):
    part = _publish(tmp_path, _part())

    widget._timer.timeout.emit()

    assert (widget._timer.isActive(), widget._timer.interval()) == (True, 100)
    assert widget.picture() == _drawn(part)


def test_once_shut_down_it_reads_nothing_more(widget):
    widget.shutdown()

    assert not widget._timer.isActive()
