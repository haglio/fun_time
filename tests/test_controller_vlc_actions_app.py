from __future__ import annotations

from pathlib import Path

from fun_time.controller_vlc_actions_app import build_parser, main


def test_build_parser_accepts_set_repeat_mode_arguments():
    args = build_parser().parse_args([
        "set-repeat-mode",
        "--port",
        "8080",
        "--password",
        "pw",
        "--target",
        "all",
    ])

    assert args.action == "set-repeat-mode"
    assert args.port == 8080
    assert args.password == "pw"
    assert args.target == "all"


def test_build_parser_accepts_current_file_path_arguments():
    args = build_parser().parse_args([
        "current-file-path",
        "--port",
        "8080",
        "--password",
        "pw",
        "--output-file",
        "state\\current.txt",
    ])

    assert args.action == "current-file-path"
    assert args.port == 8080
    assert args.output_file == "state\\current.txt"


def test_build_parser_accepts_send_command_arguments():
    args = build_parser().parse_args([
        "send-command",
        "--port",
        "8080",
        "--password",
        "pw",
        "--command",
        "pl_next",
    ])

    assert args.action == "send-command"
    assert args.port == 8080
    assert args.command == "pl_next"


def test_main_returns_zero_when_replace_playlist_succeeds(monkeypatch, tmp_path: Path):
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(
        "fun_time.controller_vlc_actions_app.replace_playlist_from_file",
        lambda port, password, playlist_path, repeat_mode="": port == 8080 and password == "pw" and Path(playlist_path) == playlist and repeat_mode == "all",
    )

    code = main([
        "replace-playlist",
        "--port",
        "8080",
        "--password",
        "pw",
        "--playlist-path",
        str(playlist),
        "--repeat-mode",
        "all",
    ])

    assert code == 0


def test_main_writes_current_file_path_output(monkeypatch, tmp_path: Path):
    output_file = tmp_path / "state" / "current.txt"
    monkeypatch.setattr(
        "fun_time.controller_vlc_actions_app.get_current_file_path",
        lambda port, password: r"C:\clips\portrait.mp4" if port == 8080 and password == "pw" else "",
    )

    code = main([
        "current-file-path",
        "--port",
        "8080",
        "--password",
        "pw",
        "--output-file",
        str(output_file),
    ])

    assert code == 0
    assert output_file.read_text(encoding="utf-8") == r"C:\clips\portrait.mp4"


def test_main_returns_zero_when_send_command_succeeds(monkeypatch):
    monkeypatch.setattr(
        "fun_time.controller_vlc_actions_app.vlc_http_cmd",
        lambda port, command, password: port == 8080 and command == "pl_next" and password == "pw",
    )

    code = main([
        "send-command",
        "--port",
        "8080",
        "--password",
        "pw",
        "--command",
        "pl_next",
    ])

    assert code == 0
