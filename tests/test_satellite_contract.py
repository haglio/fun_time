from __future__ import annotations

from dataclasses import MISSING, fields, replace
from pathlib import Path

from funestra.channel_manifest import EVERY_CHANNEL, channel_argv, channel_flag, channels_from_args
from funestra.funestra import Channels
from funestra.program.cli import build_parser

from fun_time.config import SatelliteChannel
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.manifest import CommandFiles
from fun_time.osr2_section import osr2_readout_path
from satellite.contract import (
    FILES_EACH_SATELLITE_HAS,
    SATELLITE_CHANNELS,
    SatelliteChannels,
)

_FILLED = SatelliteChannels(
    playlist=Path("state/portrait_playlist.tsv"),
    command=Path("state/portrait_cmd.txt"),
    paused=Path("state/portrait_paused.flag"),
    status=Path("state/portrait_status.json"),
    notice=Path("state/portrait_notice.txt"),
    play_points=Path("state/portrait_play_points.json"),
    hud=Path("state/portrait_hud.json"),
    dashboard_cmd=Path("state/dashboard_cmd.txt"),
    drive=Path("state/genau_drive.txt"),
    osr2_sender="portrait",
    osr2_port=50558,
    osr2_readout=Path("state/portrait_osr2_readout.txt"),
)

CHANNELS_ONLY_THE_MAIN_FUNESTRA_HAS = ("console",)
CHANNELS_ONLY_THE_BROKER_HAD = ("tcode_host", "tcode_port")
_NEVER_A_SATELLITES = {*CHANNELS_ONLY_THE_MAIN_FUNESTRA_HAS, *CHANNELS_ONLY_THE_BROKER_HAD}



def _a_satellites_parser():
    return build_parser(SATELLITE_CHANNELS)


def _the_channel_flags_it_accepts() -> set[str]:
    every_channel_flag = {channel_flag(name) for name in EVERY_CHANNEL}
    return {action.option_strings[0] for action in _a_satellites_parser()._actions
            if action.option_strings and action.option_strings[0] in every_channel_flag}


class TestTheRoundTrip:
    def test_every_channel_survives_being_spelled_out_and_parsed_back(self):
        parsed = _a_satellites_parser().parse_args(channel_argv(_FILLED, SATELLITE_CHANNELS))

        assert SatelliteChannels(**channels_from_args(parsed, SATELLITE_CHANNELS)) == _FILLED

    def test_a_satellite_given_nothing_is_given_no_flags(self):
        assert channel_argv(SatelliteChannels(), SATELLITE_CHANNELS) == []

        parsed = _a_satellites_parser().parse_args([])

        assert SatelliteChannels(**channels_from_args(parsed, SATELLITE_CHANNELS)) == SatelliteChannels()

    def test_a_channel_left_out_is_left_out_of_the_command_line(self):
        argv = channel_argv(SatelliteChannels(playlist=Path("p.tsv")), SATELLITE_CHANNELS)

        assert argv == [channel_flag("playlist"), "p.tsv"]


class TestNoDeclarationDriftsFromTheTables:
    def test_every_field_has_a_row_and_every_row_has_a_field(self):
        assert tuple(
            field.name for field in fields(SatelliteChannels) if field.name not in _NEVER_A_SATELLITES) == SATELLITE_CHANNELS

    def test_every_channel_a_funestra_has_is_given_to_a_satellite_or_named_as_not(self):
        given = set(SATELLITE_CHANNELS)

        assert not given & _NEVER_A_SATELLITES
        assert given | _NEVER_A_SATELLITES == {field.name for field in fields(Channels)}

    def test_a_satellite_accepts_exactly_the_channels_declared_here(self):
        assert _the_channel_flags_it_accepts() == {channel_flag(field) for field in SATELLITE_CHANNELS}


class TestReadOutOfTheManifest:
    def _commands(self) -> CommandFiles:
        spelled = {name: f"state/{name}" for name, field in CommandFiles.__dataclass_fields__.items()
                   if field.default is MISSING}
        return CommandFiles(**spelled)

    def _of(self, funestra: FunTimeFunestra, commands: CommandFiles | None = None) -> SatelliteChannels:
        channel = SatelliteChannel.in_state_dir(Path("state"), funestra)
        return SatelliteChannels.from_channel(channel, commands or self._commands(), funestra.label)

    def test_a_satellite_s_files_are_read_off_its_channel_rather_than_spelled_out(self):
        channels = self._of(FunTimeFunestra.PORTRAIT)

        assert channels.command == Path("state/portrait_cmd.txt")
        assert channels.paused == Path("state/portrait_paused.txt")
        assert channels.playlist == Path("state/portrait_playlist.tsv")
        assert channels.status == Path("state/portrait_status.txt")
        assert channels.notice == Path("state/portrait_notice.txt")
        assert channels.hud == Path("state/portrait_hud.json")

    def test_the_two_satellites_differ_in_every_file_of_their_own(self):
        portrait = self._of(FunTimeFunestra.PORTRAIT)
        landscape = self._of(FunTimeFunestra.LANDSCAPE)

        for field in FILES_EACH_SATELLITE_HAS:
            assert getattr(portrait, field) != getattr(landscape, field), field

    def test_the_dashboard_s_command_file_is_the_session_s_one(self):
        portrait = self._of(FunTimeFunestra.PORTRAIT)
        landscape = self._of(FunTimeFunestra.LANDSCAPE)

        assert portrait.dashboard_cmd == landscape.dashboard_cmd

    def test_each_satellite_reaches_the_osr2_through_the_brokers_slot_under_its_own_name(self):
        commands = replace(self._commands(), osr2_slot_port="50599")

        portrait = self._of(FunTimeFunestra.PORTRAIT, commands)
        landscape = self._of(FunTimeFunestra.LANDSCAPE, commands)

        assert (portrait.osr2_sender, portrait.osr2_port) == ("portrait", 50599)
        assert (landscape.osr2_sender, landscape.osr2_port) == ("landscape", 50599)

    def test_the_motion_each_satellite_draws_is_the_one_genau_publishes(self):
        commands = self._commands()

        portrait = self._of(FunTimeFunestra.PORTRAIT, commands)
        landscape = self._of(FunTimeFunestra.LANDSCAPE, commands)

        assert portrait.drive == landscape.drive == Path(commands.genau_drive_file)
        assert portrait.dashboard_cmd == Path("state/dashboard_cmd_file")

    def test_each_satellite_hands_on_its_motion_to_a_file_of_its_own_in_the_state_folder(self):
        commands = self._commands()

        portrait = self._of(FunTimeFunestra.PORTRAIT, commands)
        landscape = self._of(FunTimeFunestra.LANDSCAPE, commands)

        assert portrait.osr2_readout == osr2_readout_path(Path(commands.state_dir), "portrait")
        assert landscape.osr2_readout == osr2_readout_path(Path(commands.state_dir), "landscape")
        assert portrait.osr2_readout != landscape.osr2_readout

    def test_the_play_points_are_named_by_a_rule_rather_than_a_key(self):
        channels = SatelliteChannels.from_channel(
            SatelliteChannel.in_state_dir(Path("state"), FunTimeFunestra.PORTRAIT),
            self._commands(), "portrait", play_points=Path("state/points.json"))

        assert channels.play_points == Path("state/points.json")

    def test_no_play_points_is_answered_rather_than_invented(self):
        assert self._of(FunTimeFunestra.PORTRAIT).play_points is None
