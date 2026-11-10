from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from main_funestra.cli import (
    audio_muted,
    build_parser,
    library_source,
    mode_memory,
    play_points_path,
    resolve_playlist,
)
from main_funestra.library_source import PHASE_DISCOVER
from main_funestra.mode_memory import RememberedMode


class TestResolvePlaylist:
    def test_the_playlist_file_is_read(self, tmp_path):
        playlist = tmp_path / "main_player_playlist.tsv"
        vid = tmp_path / "a.mp4"
        vid.write_text("fake")
        playlist.write_text(f"{vid}\n", encoding="utf-8")
        args = build_parser({}).parse_args(["--playlist", str(playlist)])

        pairs = resolve_playlist(args)

        assert pairs == [(vid, None)]

    def test_playlist_file_verbatim_without_a_source(self, tmp_path):
        playlist = tmp_path / "main_player_playlist.tsv"
        a = tmp_path / "Jane-540.mp4"
        b = tmp_path / "Jane-1080p.mp4"
        a.write_text("x")
        b.write_text("x")
        playlist.write_text(f"{a}\n{b}\n", encoding="utf-8")
        args = build_parser({}).parse_args(["--playlist", str(playlist)])

        pairs = resolve_playlist(args)

        assert pairs == [(a, None), (b, None)]

    def test_an_original_and_its_upscale_fold_to_one_slot_the_larger(self, tmp_path):
        vids = tmp_path / "videos"
        vids.mkdir()
        original = vids / "Jane-540.mp4"
        upscale = vids / "Jane-540_topaz.mp4"
        original.write_text("sm")
        upscale.write_text("a much larger body")
        playlist = tmp_path / "main_player_playlist.tsv"
        playlist.write_text(f"{original}\n{upscale}\n", encoding="utf-8")
        args = build_parser({}).parse_args([
            "--playlist", str(playlist),
            "--videos-dir", str(vids),
        ])
        source = library_source(args)

        pairs = resolve_playlist(args, source=source)

        assert pairs == [(upscale, None)]

class TestLibrarySource:
    def test_none_without_dirs(self):
        args = build_parser({}).parse_args([])
        assert library_source(args) is None

    def test_built_from_dirs(self, tmp_path):
        vids = tmp_path / "videos"
        vids.mkdir()
        vid = vids / "a-1080p.mp4"
        vid.write_text("x")
        args = build_parser({}).parse_args([
            "--videos-dir", str(vids),
        ])
        assert library_source(args) is not None

    def test_the_source_is_built_beside_a_playlist_for_versions_and_compilations(self, tmp_path):
        vids = tmp_path / "videos"
        vids.mkdir()
        vid = vids / "a-1080p.mp4"
        vid.write_text("x")
        playlist = tmp_path / "main_player_playlist.tsv"
        playlist.write_text(f"{vid}\n", encoding="utf-8")
        args = build_parser({}).parse_args([
            "--playlist", str(playlist),
            "--videos-dir", str(vids),
        ])
        assert library_source(args) is not None

    def test_on_progress_reaches_the_build_whose_wait_the_loading_screen_paints(self, tmp_path):
        vids = tmp_path / "videos"
        vids.mkdir()
        vid = vids / "a-1080p.mp4"
        vid.write_text("x")
        args = build_parser({}).parse_args([
            "--videos-dir", str(vids),
        ])
        seen = []

        library_source(args, on_progress=seen.append)

        assert PHASE_DISCOVER in seen

    def test_kino_knows_nothing_from_genaus_folder(self, tmp_path):
        vids, flicks = tmp_path / "videos", tmp_path / "flicks"
        for folder in (vids, flicks / "2D" / "AI"):
            folder.mkdir(parents=True)
        scene = vids / "scene one.mp4"
        genau_flick = flicks / "2D" / "AI" / "flick one.mp4"
        for video in (scene, genau_flick):
            video.write_text("x")
        args = build_parser({}).parse_args([
            "--videos-dir", str(vids), "--flicks-dir", str(flicks),
        ])
        source = library_source(args)

        assert {entry.video for entry in source.entries} == {scene}


class TestAudioMuted:
    def test_default_is_unmuted(self, unmuted):
        args = build_parser({}).parse_args([])
        assert audio_muted(args) is False

    def test_no_audio_flag_mutes(self, unmuted):
        args = build_parser({}).parse_args(["--no-audio"])
        assert audio_muted(args) is True

    def test_env_mutes(self, monkeypatch):
        monkeypatch.setenv("FUN_TIME_MUTE_AUDIO", "1")
        args = build_parser({}).parse_args([])
        assert audio_muted(args) is True


FUN_TIME_ARGV_WRITTEN_OUT_BY_HAND = [
    "--config", "C:/example/fun_time_config.json",
    "--playlist", "C:/example/state/main_player_playlist.tsv",
    "--command-file", "C:/example/state/main_player_cmd.txt",
    "--paused-file", "C:/example/state/main_player_paused.txt",
    "--status-file", "C:/example/state/main_player_status.txt",
    "--console-file", "C:/example/state/console.json",
    "--drive-file", "C:/example/state/drive.txt",
    "--dashboard-cmd-file", "C:/example/state/dashboard_cmd.txt",
    "--notice-file", "C:/example/state/main_player_notice.txt",
    "--state-dir", "C:/example/state",
    "--x", "1920",
    "--y", "0",
    "--width", "1280",
    "--height", "1024",
    "--taskbar-identity", "Example.Orchestrator",
    "--icon", "C:/example/fun_time/icon.ico",
    "--metadata-dir", "C:/example/library/metadata",
    "--genau-command-file", "C:/example/state/genau_cmd.txt",
    "--genau-paused-file", "C:/example/state/genau_paused.txt",
    "--genau-status-file", "C:/example/state/genau_status.txt",
    "--genau-start-flick", "C:/example/library/flicks/2D/alpha one.mp4",
    "--genau-latest",
    "--genau-f-mode",
    "--genau-orientations", "portrait",
    "--origenerator-command-file", "C:/example/state/origenerator_cmd.txt",
]

LANDS_AS = {
    "config": Path("C:/example/fun_time_config.json"),
    "playlist": Path("C:/example/state/main_player_playlist.tsv"),
    "command_file": Path("C:/example/state/main_player_cmd.txt"),
    "paused_file": Path("C:/example/state/main_player_paused.txt"),
    "status_file": Path("C:/example/state/main_player_status.txt"),
    "console_file": Path("C:/example/state/console.json"),
    "drive_file": Path("C:/example/state/drive.txt"),
    "dashboard_cmd_file": Path("C:/example/state/dashboard_cmd.txt"),
    "notice_file": Path("C:/example/state/main_player_notice.txt"),
    "state_dir": Path("C:/example/state"),
    "metadata_dir": Path("C:/example/library/metadata"),
    "x": 1920,
    "y": 0,
    "width": 1280,
    "height": 1024,
    "taskbar_identity": "Example.Orchestrator",
    "icon": Path("C:/example/fun_time/icon.ico"),
    "genau_command_file": Path("C:/example/state/genau_cmd.txt"),
    "genau_paused_file": Path("C:/example/state/genau_paused.txt"),
    "genau_status_file": Path("C:/example/state/genau_status.txt"),
    "genau_start_flick": "C:/example/library/flicks/2D/alpha one.mp4",
    "genau_latest": True,
    "genau_f_mode": True,
    "genau_orientations": ["portrait"],
    "origenerator_command_file": Path("C:/example/state/origenerator_cmd.txt"),
}


class TestTheCommandLineFunTimeLaunchesMainFunestraWith:
    @pytest.mark.parametrize(("name", "expected"), sorted(LANDS_AS.items()))
    def test_each_flag_lands_on_the_namespace_as_itself(self, name, expected):
        args = build_parser({}).parse_args(FUN_TIME_ARGV_WRITTEN_OUT_BY_HAND)

        landed = getattr(args, name)

        assert landed == expected
        assert type(landed) is type(expected), (
            f"--{name.replace('_', '-')} changed type: a str reaches Path() later, "
            "a Path reaches an argparse default nobody set")

    def test_the_whole_line_fun_time_writes_parses(self):
        assert build_parser({}).parse_args(FUN_TIME_ARGV_WRITTEN_OUT_BY_HAND) is not None

    def test_a_line_without_metadata_dir_still_parses(self):
        without = [a for a in FUN_TIME_ARGV_WRITTEN_OUT_BY_HAND
                   if a not in ("--metadata-dir", "C:/example/library/metadata")]

        args = build_parser({}).parse_args(without)

        assert args.metadata_dir is None


class TestTheRestOfTheFlags:
    @pytest.mark.parametrize("option", ["taskbar_identity", "notice_file"])
    def test_what_the_app_reads_as_a_plain_attribute_the_parser_always_defines(self, option):
        args = build_parser({}).parse_args([])

        assert hasattr(args, option)

    def test_the_library_directories_and_the_silent_run(self, tmp_path):
        args = build_parser({}).parse_args([
            "--videos-dir", str(tmp_path / "videos"),
            "--flicks-dir", str(tmp_path / "flicks"),
            "--state-dir", str(tmp_path / "state"),
            "--notice-file", str(tmp_path / "notice.txt"),
            "--no-audio",
        ])

        assert args.videos_dir == tmp_path / "videos"
        assert args.flicks_dir == tmp_path / "flicks"
        assert args.state_dir == tmp_path / "state"
        assert args.notice_file == tmp_path / "notice.txt"
        assert audio_muted(args) is True


EVERY_FLAG_ARGV = [
    "--config", "C:/example/fun_time_config.json",
    "--videos-dir", "C:/example/library/videos",
    "--flicks-dir", "C:/example/library/flicks",
    "--state-dir", "C:/example/state",
    "--metadata-dir", "C:/example/library/metadata",
    "--notice-file", "C:/example/state/main_player_notice.txt",
    "--origenerator-command-file", "C:/example/state/origenerator_cmd.txt",
    "--playlist", "C:/example/state/main_player_playlist.tsv",
    "--width", "1280",
    "--height", "1024",
    "--x", "1920",
    "--y", "0",
    "--osr2-port", "51000",
    "--command-file", "C:/example/state/main_player_cmd.txt",
    "--paused-file", "C:/example/state/main_player_paused.txt",
    "--status-file", "C:/example/state/main_player_status.txt",
    "--console-file", "C:/example/state/console.json",
    "--drive-file", "C:/example/state/drive.txt",
    "--dashboard-cmd-file", "C:/example/state/dashboard_cmd.txt",
    "--genau-command-file", "C:/example/state/genau_cmd.txt",
    "--genau-paused-file", "C:/example/state/genau_paused.txt",
    "--genau-status-file", "C:/example/state/genau_status.txt",
    "--genau-start-flick", "C:/example/library/flicks/2D/alpha one.mp4",
    "--genau-latest",
    "--genau-f-mode",
    "--genau-orientations", "portrait",
    "--notify-host", "10.0.0.8",
    "--notify-port", "51001",
    "--no-audio",
    "--taskbar-identity", "Example.Orchestrator",
    "--icon", "C:/example/fun_time/icon.ico",
]

EVERY_FLAG_LANDS_AS = {
    "config": Path("C:/example/fun_time_config.json"),
    "videos_dir": Path("C:/example/library/videos"),
    "flicks_dir": Path("C:/example/library/flicks"),
    "state_dir": Path("C:/example/state"),
    "metadata_dir": Path("C:/example/library/metadata"),
    "notice_file": Path("C:/example/state/main_player_notice.txt"),
    "origenerator_command_file": Path("C:/example/state/origenerator_cmd.txt"),
    "playlist": Path("C:/example/state/main_player_playlist.tsv"),
    "width": 1280,
    "height": 1024,
    "x": 1920,
    "y": 0,
    "osr2_port": 51000,
    "command_file": Path("C:/example/state/main_player_cmd.txt"),
    "paused_file": Path("C:/example/state/main_player_paused.txt"),
    "status_file": Path("C:/example/state/main_player_status.txt"),
    "console_file": Path("C:/example/state/console.json"),
    "drive_file": Path("C:/example/state/drive.txt"),
    "dashboard_cmd_file": Path("C:/example/state/dashboard_cmd.txt"),
    "genau_command_file": Path("C:/example/state/genau_cmd.txt"),
    "genau_paused_file": Path("C:/example/state/genau_paused.txt"),
    "genau_status_file": Path("C:/example/state/genau_status.txt"),
    "genau_start_flick": "C:/example/library/flicks/2D/alpha one.mp4",
    "genau_latest": True,
    "genau_f_mode": True,
    "genau_orientations": ["portrait"],
    "notify_host": "10.0.0.8",
    "notify_port": 51001,
    "no_audio": True,
    "taskbar_identity": "Example.Orchestrator",
    "icon": Path("C:/example/fun_time/icon.ico"),
}

NO_FLAGS_LANDS_AS = {
    "config": None,
    "videos_dir": None,
    "flicks_dir": None,
    "state_dir": None,
    "metadata_dir": None,
    "notice_file": None,
    "playlist": None,
    "width": 1200,
    "height": 900,
    "x": None,
    "y": None,
    "osr2_port": None,
    "command_file": None,
    "paused_file": None,
    "status_file": None,
    "console_file": None,
    "drive_file": None,
    "dashboard_cmd_file": None,
    "origenerator_command_file": None,
    "genau_command_file": None,
    "genau_paused_file": None,
    "genau_status_file": None,
    "genau_start_flick": None,
    "genau_latest": False,
    "genau_f_mode": False,
    "genau_orientations": None,
    "notify_host": "127.0.0.1",
    "notify_port": 50556,
    "no_audio": False,
    "taskbar_identity": None,
    "icon": None,
}


class TestTheWholeSurfaceFunTimeLaunchesThrough:
    def test_an_unflagged_launch_gets_these_defaults_and_nothing_else(self):
        assert vars(build_parser({}).parse_args([])) == NO_FLAGS_LANDS_AS

    def test_every_flag_it_offers_lands_on_the_name_the_app_reads(self):
        assert vars(build_parser({}).parse_args(EVERY_FLAG_ARGV)) == EVERY_FLAG_LANDS_AS

    def test_genau_tells_the_audio_companion_where_fun_times_config_puts_it(self):
        args = build_parser({"audio_companion": {"host": "10.0.0.9", "port": 50600}}).parse_args([])

        assert (args.notify_host, args.notify_port) == ("10.0.0.9", 50600)


class TestTheConfigsOwnDefaults:
    CONFIG: ClassVar[dict] = {
        "main_player": {
            "videos_dir": "C:/example/library/videos",
            "metadata_dir": "C:/example/library/metadata",
        },
    }

    @pytest.mark.parametrize(("name", "expected"), [
        ("videos_dir", Path("C:/example/library/videos")),
        ("metadata_dir", Path("C:/example/library/metadata")),
    ])
    def test_a_configured_value_is_what_the_flag_would_have_said(self, name, expected):
        args = build_parser(self.CONFIG).parse_args([])

        assert getattr(args, name) == expected

    def test_a_flag_beats_the_configured_value(self):
        args = build_parser(self.CONFIG).parse_args(["--videos-dir", "C:/example/elsewhere"])

        assert args.videos_dir == Path("C:/example/elsewhere")

    def test_genaus_folder_and_the_state_folder_come_only_from_the_launch(self):
        args = build_parser({"flicks_dir": "C:/example/a", "state_dir": "C:/example/b",
                             "main_player": {"flicks_dir": "C:/example/c"}}).parse_args([])

        assert (args.flicks_dir, args.state_dir) == (None, None)


class TestWhereMainFunestraKeepsItsState:
    def test_the_state_dir_holds_the_mode_it_was_last_in(self, tmp_path):
        args = build_parser({}).parse_args(["--state-dir", str(tmp_path)])

        mode_memory(args).write(RememberedMode(compilation="Vol6"))

        assert (tmp_path / "main_player_mode.txt").exists()

    def test_the_state_dir_holds_where_each_video_was_left(self, tmp_path):
        args = build_parser({}).parse_args(["--state-dir", str(tmp_path)])

        assert play_points_path(args) == tmp_path / "main_player_play_points.json"

    def test_run_by_hand_it_keeps_its_state_beside_its_config_not_where_it_started(self, tmp_path):
        config = tmp_path / "fun_time_config.json"
        config.write_text("{}", encoding="utf-8")
        args = build_parser({}).parse_args(["--config", str(config)])

        mode_memory(args).write(RememberedMode(compilation="Vol6"))

        assert (tmp_path / "main_player_mode.txt").exists()
