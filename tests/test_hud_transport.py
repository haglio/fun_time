from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from funestra.hud_button import Button
from funestra.hud_kinds import HudFacts, HudKind, hud_rows
from funestra.modes import Osr2State
from funestra.satellite_hud import HudModel

from fun_time.hud_transport import HudPublisher, hosted_model
from fun_time.shared_state import SatelliteState


def _rows(model: HudModel) -> list[list[str]]:
    return [[button.command for button in row] for row in model.rows]


def test_a_hosted_hud_leads_with_the_funestras_own_minimize_button_and_crown():
    hosted = HudModel(funestra="portrait", rows=((Button("portrait_next", "N", "Next slide"),),))

    def worn(*, in_vr: bool) -> HudModel:
        return hosted_model("portrait", hosted, SatelliteState(), in_vr=in_vr, crowned=False,
                            active=False, osr2=None)

    assert _rows(worn(in_vr=False)) == [["portrait_minimize", "portrait_crown"], ["portrait_next"]]
    assert _rows(worn(in_vr=True)) == [["portrait_next"]]


def test_a_satellite_the_app_has_not_filled_wears_only_its_own_window_buttons():
    model = hosted_model("landscape", None, SatelliteState(hud_minimized=True), in_vr=False,
                         crowned=False, active=True, osr2=None)

    assert (model.title, model.hud_minimized, model.active) == ("", True, True)
    assert _rows(model) == [["landscape_minimize"]]


def test_a_hud_with_no_facts_yet_offers_the_osr2_beside_the_hosted_rows():
    hosted = HudModel(funestra="portrait", rows=((Button("portrait_next", "N", "Next slide"),),))

    held = hosted_model("portrait", hosted, SatelliteState(), in_vr=False, crowned=False,
                        active=False, osr2=Osr2State.ROBOT_HAND)
    offered = hosted_model("portrait", hosted, SatelliteState(), in_vr=False, crowned=False,
                           active=False, osr2=None)

    assert [(button.command, button.lit) for row in held.osr2_rows for button in row] == [
        ("portrait_take_osr2", True)]
    assert [(button.command, button.lit) for row in offered.osr2_rows for button in row] == [
        ("portrait_take_osr2", False)]
    assert (held.osr2, offered.osr2) == (Osr2State.ROBOT_HAND, "")


_SLIDESHOW = HudModel(funestra="portrait", kind=HudKind.SLIDESHOW,
                      facts=HudFacts(funestra="portrait", hosted=True, locked=True,
                                     crowned=True, has_osr2=True, in_vr=True))


def _satellite(*, in_vr: bool = False, crowned: bool = False,
               osr2: Osr2State | None = None) -> HudModel:
    return hosted_model("portrait", _SLIDESHOW, SatelliteState(), in_vr=in_vr, crowned=crowned,
                        active=False, osr2=osr2)


def test_a_published_slideshow_becomes_the_satellites_own_kind_of_hud():
    satellite = _satellite()

    assert satellite.kind is HudKind.SATELLITE
    assert satellite.facts.locked is True


def test_the_satellite_says_what_only_fun_time_knows_over_what_origenerator_published():
    assert (_satellite().facts.crowned, _satellite().facts.has_osr2,
            _satellite().facts.in_vr) == (False, False, False)
    held = _satellite(in_vr=True, crowned=True, osr2=Osr2State.FUNSCRIPT)
    assert (held.facts.crowned, held.facts.has_osr2, held.facts.in_vr) == (True, True, True)
    assert held.osr2 == Osr2State.FUNSCRIPT


def test_the_satellite_adds_no_button_of_its_own_to_funestras_list():
    satellite = _satellite(crowned=True, osr2=Osr2State.ROBOT_HAND)

    assert satellite.rows == hud_rows(HudKind.SATELLITE, satellite.facts)
    assert satellite.osr2_rows == ()


def test_the_satellite_s_facts_say_its_item_is_a_favorite_as_the_slideshow_published():
    published = replace(_SLIDESHOW, is_favorite=True)

    worn = hosted_model("portrait", published, SatelliteState(), in_vr=False, crowned=False,
                        active=False, osr2=None)

    assert (worn.facts.favorite, _satellite().facts.favorite) == (True, False)


def test_the_satellite_answers_to_its_own_name_whatever_the_slideshow_called_itself():
    published = replace(_SLIDESHOW, funestra="landscape",
                        facts=replace(_SLIDESHOW.facts, funestra="landscape"))

    worn = hosted_model("portrait", published, SatelliteState(), in_vr=False, crowned=False,
                        active=False, osr2=None)

    assert (worn.funestra, worn.facts.funestra) == ("portrait", "portrait")


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
