from __future__ import annotations

from pathlib import Path

from fun_time.controller_modes_app import build_parser, main


def test_build_parser_accepts_playlist_arguments():
    args = build_parser().parse_args([
        "write-fmode-playlists",
        "--primary-sources",
        "primary",
        "--portrait-sources",
        "portrait",
        "--landscape-sources",
        "landscape",
        "--favs-file",
        "favs.csv",
        "--state-dir",
        "state",
        "--enabled",
        "1",
    ])

    assert args.action == "write-fmode-playlists"
    assert args.primary_sources == "primary"
    assert args.enabled == "1"


def test_main_returns_success_exit_code_when_playlists_written(tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    for root in (primary_root, portrait_root, landscape_root):
        root.mkdir(parents=True)
    (primary_root / "main.mp4").write_text("x", encoding="utf-8")
    (portrait_root / "portrait.mp4").write_text("x", encoding="utf-8")
    (landscape_root / "landscape.mp4").write_text("x", encoding="utf-8")
    mirrored = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "main.funscript"
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    mirrored.write_text("{}", encoding="utf-8")
    favs = tmp_path / "favs.csv"
    favs.write_text(
        f'local_file,web_url\r\n"x","{portrait_root / "portrait.mp4"}"\r\n"x","{landscape_root / "landscape.mp4"}"\r\n',
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"

    code = main([
        "write-fmode-playlists",
        "--primary-sources",
        str(primary_root),
        "--portrait-sources",
        str(portrait_root),
        "--landscape-sources",
        str(landscape_root),
        "--favs-file",
        str(favs),
        "--state-dir",
        str(state_dir),
        "--enabled",
        "1",
    ])

    assert code == 0
    assert (state_dir / "portrait_vlc_playlist.m3u").exists()


def test_main_returns_empty_playlist_exit_code_when_filtered_result_is_empty(tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    for root in (primary_root, portrait_root, landscape_root):
        root.mkdir(parents=True)
    (primary_root / "main.mp4").write_text("x", encoding="utf-8")
    (portrait_root / "portrait.mp4").write_text("x", encoding="utf-8")
    (landscape_root / "landscape.mp4").write_text("x", encoding="utf-8")
    favs = tmp_path / "favs.csv"
    favs.write_text("local_file,web_url\r\n", encoding="utf-8")

    code = main([
        "write-fmode-playlists",
        "--primary-sources",
        str(primary_root),
        "--portrait-sources",
        str(portrait_root),
        "--landscape-sources",
        str(landscape_root),
        "--favs-file",
        str(favs),
        "--state-dir",
        str(tmp_path / "state"),
        "--enabled",
        "1",
    ])

    assert code == 3
