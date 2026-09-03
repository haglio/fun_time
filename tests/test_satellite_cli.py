from __future__ import annotations

from pathlib import Path

from satellite.cli import audio_muted, build_parser, resolve_playlist


def test_resolve_playlist_reads_videos_dropping_funscripts(tmp_path):
    pl = tmp_path / "portrait.tsv"
    pl.write_text("a.mp4\nb.mp4\tb.funscript\n", encoding="utf-8")
    args = build_parser().parse_args(["--playlist", str(pl)])

    assert resolve_playlist(args) == [Path("a.mp4"), Path("b.mp4")]


def test_resolve_playlist_without_a_file_is_empty():
    args = build_parser().parse_args([])
    assert resolve_playlist(args) == []


def test_audio_muted_from_the_flag():
    assert audio_muted(build_parser().parse_args(["--no-audio"])) is True
    assert audio_muted(build_parser().parse_args([])) is False


def test_audio_muted_from_the_env_contract(monkeypatch):
    monkeypatch.setenv("FUN_TIME_MUTE_AUDIO", "1")
    assert audio_muted(build_parser().parse_args([])) is True


def test_parser_accepts_the_file_quartet_and_geometry(tmp_path):
    args = build_parser().parse_args([
        "--playlist", str(tmp_path / "p.tsv"),
        "--command-file", str(tmp_path / "cmd.txt"),
        "--paused-file", str(tmp_path / "paused.txt"),
        "--status-file", str(tmp_path / "status.txt"),
        "--x", "2560", "--y", "0", "--width", "1440", "--height", "2500",
    ])
    assert args.command_file == tmp_path / "cmd.txt"
    assert args.paused_file == tmp_path / "paused.txt"
    assert args.status_file == tmp_path / "status.txt"
    assert (args.x, args.y, args.width, args.height) == (2560, 0, 1440, 2500)


def test_title_defaults_to_satellite():
    """Standalone (no --title) keeps the plain caption used before fun_time
    needed to tell the two apart."""
    assert build_parser().parse_args([]).title == "Satellite"


def test_title_is_taken_from_the_flag():
    """fun_time gives each satellite a distinct caption so the sequencer can
    resolve each window to its portrait/landscape slot; a shared title crosses
    them, which is the visual swap this prevents."""
    args = build_parser().parse_args(["--title", "Portrait AI Player"])
    assert args.title == "Portrait AI Player"


def test_parser_takes_the_hud_panel_and_command_files():
    """The lock HUD is drawn inside the player, so fun_time hands it the panel to
    render and the command file its clicks post back to."""
    args = build_parser().parse_args(
        ["--hud-file", "state/portrait_hud.json",
         "--dashboard-cmd-file", "state/dashboard_cmd.txt"]
    )

    assert args.hud_file == Path("state/portrait_hud.json")
    assert args.dashboard_cmd_file == Path("state/dashboard_cmd.txt")


def test_the_hud_files_are_optional():
    args = build_parser().parse_args([])

    assert args.hud_file is None
    assert args.dashboard_cmd_file is None
