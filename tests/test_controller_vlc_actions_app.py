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
