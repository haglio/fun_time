from __future__ import annotations

from dataclasses import MISSING, fields, replace
from pathlib import Path

from funestra.funestra import Channels

from fun_time.config import SatelliteChannel
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.manifest import CommandFiles
from fun_time.osr2_section import osr2_readout_path
from satellite.cli import build_parser
from satellite.contract import (
    CHANNELS,
    PLACEMENT,
    SatelliteChannels,
    WindowPlacement,
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

_PLACED = WindowPlacement(x=2560, y=0, width=1440, height=2500,
                          title="Portrait AI Funestra", taskbar_identity="Example.App")


class TestTheRoundTrip:
    def test_every_channel_survives_being_spelled_out_and_parsed_back(self):
        parsed = build_parser().parse_args(_FILLED.to_argv())

        assert SatelliteChannels.from_args(parsed) == _FILLED

    def test_every_part_of_the_placement_survives_it_too(self):
        parsed = build_parser().parse_args(_PLACED.to_argv())

        assert WindowPlacement.from_args(parsed) == _PLACED

    def test_a_satellite_given_nothing_is_given_no_flags(self):
        assert SatelliteChannels().to_argv() == []

        parsed = build_parser().parse_args([])

        assert SatelliteChannels.from_args(parsed) == SatelliteChannels()
        assert WindowPlacement.from_args(parsed) == WindowPlacement()

    def test_a_channel_left_out_is_left_out_of_the_command_line(self):
        argv = SatelliteChannels(playlist=Path("p.tsv")).to_argv()

        assert argv == ["--playlist", "p.tsv"]


class TestNothingHasDrifted:
    def test_every_field_has_a_row_and_every_row_has_a_field(self):
        assert tuple(field for field, _flag, _kind in CHANNELS) == tuple(
            field.name for field in fields(SatelliteChannels) if field.name not in _NEVER_A_SATELLITES)
        assert tuple(field for field, _flag in PLACEMENT) == tuple(
            field.name for field in fields(WindowPlacement))

    def test_every_channel_a_funestra_has_is_given_to_a_satellite_or_named_as_not(self):
        given = {field for field, _flag, _kind in CHANNELS}

        assert not given & _NEVER_A_SATELLITES
        assert given | _NEVER_A_SATELLITES == {field.name for field in fields(Channels)}

    def test_every_flag_declared_here_is_one_the_satellite_accepts(self):
        accepted = {action.option_strings[0]
                    for action in build_parser()._actions if action.option_strings}

        declared = {flag for _field, flag, _kind in CHANNELS if flag is not None}
        declared |= {flag for _field, flag in PLACEMENT}

        assert declared <= accepted, declared - accepted

    def test_the_satellite_accepts_nothing_a_record_cannot_carry(self):
        accepted = {action.option_strings[0]
                    for action in build_parser()._actions if action.option_strings}
        declared = {flag for _field, flag, _kind in CHANNELS if flag is not None}
        declared |= {flag for _field, flag in PLACEMENT}
        assert accepted - declared - {"-h", "--no-audio"} == set()


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

        own = [field for field, _flag, kind in CHANNELS if kind is not None]
        for field in own:
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
