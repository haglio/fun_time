from __future__ import annotations

from funestra.file_channel import publish_whole
from funestra.hud_button import Button
from funestra.hud_controls import reset_button
from funestra.satellite_hud import HudModel, hud_text

from fun_time import funestra_handover
from fun_time.config import SatelliteChannel
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.funestra_handover import show_has_something_to_reset, shows_button_lit

LANDSCAPE = FunTimeFunestra.LANDSCAPE


def _publish(channel: SatelliteChannel, *, favorites: bool, nothing_to_reset: bool) -> None:
    label = LANDSCAPE.label
    hud = HudModel(funestra=label, rows=((
        Button(f"{label}_favorites", "F", "Favorites", lit=favorites),
        reset_button(label, nothing_to_reset=nothing_to_reset, tooltip="Reset"),
    ),))
    assert publish_whole(channel.origenerator_hud_file, hud_text(hud))


def _counting_parses(monkeypatch) -> list[str]:
    parsed: list[str] = []
    real = funestra_handover.parse_hud

    def counted(text: str):
        parsed.append(text)
        return real(text)

    monkeypatch.setattr(funestra_handover, "parse_hud", counted)
    return parsed


def test_a_hud_nobody_republished_is_parsed_once_over_many_passes(tmp_path, monkeypatch):
    channel = SatelliteChannel.in_state_dir(tmp_path, LANDSCAPE)
    _publish(channel, favorites=True, nothing_to_reset=False)
    parsed = _counting_parses(monkeypatch)

    answers = [(shows_button_lit(channel, "favorites"), show_has_something_to_reset(channel))
               for _ in range(3)]

    assert answers == [(True, True)] * 3
    assert len(parsed) == 1


def test_a_hud_republished_is_read_again(tmp_path, monkeypatch):
    channel = SatelliteChannel.in_state_dir(tmp_path, LANDSCAPE)
    _publish(channel, favorites=True, nothing_to_reset=False)
    assert shows_button_lit(channel, "favorites") is True

    _publish(channel, favorites=False, nothing_to_reset=True)

    assert (shows_button_lit(channel, "favorites"), show_has_something_to_reset(channel)) == (
        False, False)


def test_no_hud_published_reads_as_no_button(tmp_path):
    channel = SatelliteChannel.in_state_dir(tmp_path, LANDSCAPE)

    assert (shows_button_lit(channel, "favorites"), show_has_something_to_reset(channel)) == (
        None, False)
