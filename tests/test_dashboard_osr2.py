from __future__ import annotations

from pathlib import Path

import pytest
from funestra.main_hud import MainHudModel
from funestra.main_hud_painter import MainHud
from funestra.osr2_controls_host import Osr2ControlsHost
from funestra.osr2_visualizer import DriveHud

from fun_time.dashboard_osr2 import Osr2PartWidget
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.osr2_section import osr2_part_path, osr2_readout_path
from tests.made_up_osr2_parts import a_part, a_readout, handed_on, published
from tests.standing_files import left_to_stand


def _drawn(part: MainHudModel, readout: DriveHud | None = None):
    return Osr2ControlsHost(post=[].append).drawing(MainHud(main_hud_model=part, drive=readout))


@pytest.fixture
def widget(tmp_path):
    part = Osr2PartWidget(tmp_path)
    try:
        yield part
    finally:
        part.shutdown()
        part.close()


def test_it_draws_the_part_the_app_publishes_with_its_holders_readout(widget, tmp_path):
    part = published(tmp_path, a_part(FunTimeFunestra.PORTRAIT))
    readout = handed_on(tmp_path, FunTimeFunestra.PORTRAIT, a_readout())
    handed_on(tmp_path, FunTimeFunestra.MAIN, a_readout(speed=90))

    widget.poll()

    assert widget.drawing() == _drawn(part, readout)


def test_until_the_app_publishes_a_part_it_draws_nothing(widget):
    widget.poll()
    raw, _size = widget.drawing()

    assert set(raw[3::4]) == {0}


def test_a_read_that_changes_what_it_draws_repaints_it_and_one_that_does_not_repaints_nothing(
        widget, tmp_path, monkeypatch):
    repaints = []
    monkeypatch.setattr(widget, "update", lambda: repaints.append("repaint"))
    drawings = iter([(b"first", (1, 1)), (b"first", (1, 1)), (b"walked", (1, 1))])
    monkeypatch.setattr(widget, "drawing", lambda: next(drawings))

    widget.poll()
    published(tmp_path, a_part())
    for _ in range(3):
        widget.poll()

    assert repaints == ["repaint"] * 2


def test_reads_with_nothing_republished_open_neither_file_again(widget, tmp_path, monkeypatch):
    published(tmp_path, a_part())
    handed_on(tmp_path, FunTimeFunestra.MAIN, a_readout())
    left_to_stand(osr2_part_path(tmp_path), osr2_readout_path(tmp_path, FunTimeFunestra.MAIN.label))
    opened = []
    real_read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text",
                        lambda self, *a, **k: (opened.append(self.name), real_read_text(self, *a, **k))[1])

    for _ in range(3):
        widget.poll()

    assert len(opened) == 2


def test_it_holds_the_app_a_whole_part_takes_before_one_has_arrived(widget, tmp_path):
    reserved = widget.size()

    published(tmp_path, a_part())
    handed_on(tmp_path, FunTimeFunestra.MAIN, a_readout())
    widget.poll()
    widget.grab()

    assert widget.size() == reserved
    assert (reserved.width(), reserved.height()) == _drawn(a_part(), a_readout())[1]


def test_it_reads_the_part_by_itself_ten_times_a_second(widget, tmp_path):
    part = published(tmp_path, a_part())

    widget._timer.timeout.emit()

    assert (widget._timer.isActive(), widget._timer.interval()) == (True, 100)
    assert widget.drawing() == _drawn(part)


def test_once_shut_down_it_reads_nothing_more(widget):
    widget.shutdown()

    assert not widget._timer.isActive()
