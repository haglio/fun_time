"""The group of values a satellite is launched with, held to all three readers.

They were declared three times over in modules that do not reference each other
-- argparse flags in ``satellite/cli.py``, an argv builder in
``fun_time/windows_bridge_startup.py``, and a third reading out of the launch
manifest by the VR player's own in-process satellite -- so adding one file to
the contract meant finding all three.  What holds them together now is the
round trip: a record spelled into argv and parsed straight back must be the
record it started as, which no spelling can survive being wrong on one side of.
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from fun_time.manifest import CommandFiles
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
    play_points=Path("state/portrait_play_points.json"),
    hud=Path("state/portrait_hud.json"),
    dashboard_cmd=Path("state/dashboard_cmd.txt"),
)

_PLACED = WindowPlacement(x=2560, y=0, width=1440, height=2500,
                          title="Portrait AI Player", taskbar_identity="Example.App")


class TestTheRoundTrip:
    """What a satellite is given, and what it reads, are the same list."""

    def test_every_channel_survives_being_spelled_out_and_parsed_back(self):
        parsed = build_parser().parse_args(_FILLED.to_argv())

        assert SatelliteChannels.from_args(parsed) == _FILLED

    def test_every_part_of_the_placement_survives_it_too(self):
        parsed = build_parser().parse_args(_PLACED.to_argv())

        assert WindowPlacement.from_args(parsed) == _PLACED

    def test_a_satellite_given_nothing_is_given_no_flags(self):
        """Standalone: no session, no files, and a window at the defaults."""
        assert SatelliteChannels().to_argv() == []

        parsed = build_parser().parse_args([])

        assert SatelliteChannels.from_args(parsed) == SatelliteChannels()
        assert WindowPlacement.from_args(parsed) == WindowPlacement()

    def test_a_channel_left_out_is_left_out_of_the_command_line(self):
        """A session that draws no lock panel hands over neither the panel nor
        the file a click on it would post to."""
        argv = SatelliteChannels(playlist=Path("p.tsv")).to_argv()

        assert argv == ["--playlist", "p.tsv"]


class TestNothingHasDrifted:
    """Each table below is the one place its group is written down, so the
    checks here are what stop a fourth declaration growing back."""

    def test_every_field_has_a_row_and_every_row_has_a_field(self):
        assert tuple(field for field, _flag, _kind in CHANNELS) == tuple(
            field.name for field in fields(SatelliteChannels))
        assert tuple(field for field, _flag in PLACEMENT) == tuple(
            field.name for field in fields(WindowPlacement))

    def test_every_flag_declared_here_is_one_the_satellite_accepts(self):
        """The flags are written into a real process's command line, so a
        spelling that only this module believes in is a player that will not
        start."""
        accepted = {action.option_strings[0]
                    for action in build_parser()._actions if action.option_strings}

        declared = {flag for _field, flag, _kind in CHANNELS}
        declared |= {flag for _field, flag in PLACEMENT}

        assert declared <= accepted, declared - accepted

    def test_the_satellite_accepts_nothing_a_record_cannot_carry(self):
        """The other way round, which is the direction a new flag drifts: one
        added to the parser alone is a value the launcher can never pass."""
        accepted = {action.option_strings[0]
                    for action in build_parser()._actions if action.option_strings}
        declared = {flag for _field, flag, _kind in CHANNELS}
        declared |= {flag for _field, flag in PLACEMENT}
        # --help is argparse's own, and --no-audio is a policy rather than a
        # channel or a placement: a satellite opens muted either way, and the
        # flag is the permanent silence.
        assert accepted - declared - {"-h", "--no-audio"} == set()


class TestReadOutOfTheManifest:
    """The VR player runs its satellites in its own process, so it is handed
    these rather than an argv -- the same group, through the manifest's own
    per-side lookup."""

    def _commands(self) -> CommandFiles:
        spelled = {name: f"state/{name}" for name in CommandFiles.__dataclass_fields__}
        return CommandFiles(**spelled)

    def test_a_side_s_files_are_read_by_side_rather_than_spelled_out(self):
        channels = SatelliteChannels.from_manifest(self._commands(), "portrait")

        assert channels.command == Path("state/portrait_cmd_file")
        assert channels.paused == Path("state/portrait_paused_file")
        assert channels.playlist == Path("state/portrait_playlist_file")
        assert channels.status == Path("state/portrait_status_file")
        assert channels.hud == Path("state/portrait_hud_file")

    def test_the_two_sides_differ_in_every_file_of_their_own(self):
        commands = self._commands()

        portrait = SatelliteChannels.from_manifest(commands, "portrait")
        landscape = SatelliteChannels.from_manifest(commands, "landscape")

        own = [field for field, _flag, kind in CHANNELS if kind is not None]
        for field in own:
            assert getattr(portrait, field) != getattr(landscape, field), field

    def test_the_dashboard_s_command_file_is_the_session_s_one(self):
        """Shared by every child rather than carried per side: a click on any
        lock panel posts to the same place."""
        commands = self._commands()

        portrait = SatelliteChannels.from_manifest(commands, "portrait")
        landscape = SatelliteChannels.from_manifest(commands, "landscape")

        assert portrait.dashboard_cmd == landscape.dashboard_cmd
        assert portrait.dashboard_cmd == Path("state/dashboard_cmd_file")

    def test_the_play_points_are_named_by_a_rule_rather_than_a_key(self):
        """The manifest does not carry one: where a side left each clip is
        named from the state directory, which is why it is passed in."""
        channels = SatelliteChannels.from_manifest(
            self._commands(), "portrait", play_points=Path("state/points.json"))

        assert channels.play_points == Path("state/points.json")

    def test_no_play_points_is_answered_rather_than_invented(self):
        assert SatelliteChannels.from_manifest(
            self._commands(), "portrait").play_points is None
