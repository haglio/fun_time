from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from funestra.hud_button import Button
from funestra.satellite_hud import HudModel

from fun_time.hud_transport import ORIGENERATOR_MODE_LABEL, HudPublisher, hosted_model
from fun_time.shared_state import SatelliteState


def _rows(model: HudModel) -> list[list[str]]:
    return [[button.command for button in row] for row in model.rows]


def test_a_hosted_hud_leads_with_the_funestras_own_minimize_button_and_crown():
    hosted = HudModel(funestra="portrait", rows=((Button("portrait_next", "N", "Next slide"),),))

    def worn(*, in_vr: bool) -> HudModel:
        return hosted_model("portrait", hosted, SatelliteState(), in_vr=in_vr, crowned=False,
                            active=False)

    assert _rows(worn(in_vr=False)) == [["portrait_minimize", "portrait_crown"], ["portrait_next"]]
    assert _rows(worn(in_vr=True)) == [["portrait_next"]]


def test_before_the_app_has_shown_its_hud_a_satellite_wears_a_title_and_its_own_buttons():
    model = hosted_model("landscape", None, SatelliteState(hud_minimized=True), in_vr=False,
                         crowned=False, active=True)

    assert (model.title, model.hud_minimized, model.active) == (ORIGENERATOR_MODE_LABEL, True, True)
    assert _rows(model) == [["landscape_minimize"]]


def test_publish_writes_the_file_only_when_the_hud_changes(tmp_path: Path):
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file})

    assert publisher.publish_text("portrait", "one") is True
    assert publisher.publish_text("portrait", "one") is False
    assert publisher.publish_text("portrait", "two") is True

    assert hud_file.read_text(encoding="utf-8") == "two"


def test_a_hud_polled_every_frame_is_swapped_in_whole_never_written_in_place(tmp_path: Path):
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file})
    opened_to_write: list[Path] = []
    real_open = Path.open

    def noting(path, mode="r", *args, **kwargs):
        if "w" in mode:
            opened_to_write.append(path)
        return real_open(path, mode, *args, **kwargs)

    with patch.object(Path, "open", noting):
        assert publisher.publish_text("portrait", "one") is True

    assert opened_to_write
    assert hud_file not in opened_to_write
    assert hud_file.read_text(encoding="utf-8") == "one"


def test_a_locked_huds_dropped_write_is_retried_next_tick(tmp_path: Path):
    hud_file = tmp_path / "portrait_hud.json"
    publisher = HudPublisher({"portrait": hud_file})

    with patch("fun_time.hud_transport.publish_whole", return_value=False):
        assert publisher.publish_text("portrait", "one") is False
    assert publisher.publish_text("portrait", "one") is True

    assert hud_file.read_text(encoding="utf-8") == "one"


def test_publish_ignores_a_satellite_with_no_file():
    assert HudPublisher({}).publish_text("portrait", "one") is False
