from __future__ import annotations

import configparser
from pathlib import Path

import fun_time.windows_bridge_modes_app as controller_modes_app


def test_build_parser_accepts_playlist_arguments():
    args = controller_modes_app.build_parser().parse_args([
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

    code = controller_modes_app.main([
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



def test_apply_fmode_replaces_all_three_playlists_and_writes_result(monkeypatch, tmp_path: Path):
    result_file = tmp_path / "result.ini"
    calls: list[tuple] = []

    monkeypatch.setattr(
        controller_modes_app,
        "build_fmode_playlists",
        lambda **kwargs: type(
            "Plan",
            (),
            {
                "success": True,
                "primary_playlist_path": tmp_path / "primary_vlc_playlist.m3u",
                "portrait_playlist_path": tmp_path / "portrait_vlc_playlist.m3u",
                "landscape_playlist_path": tmp_path / "landscape_vlc_playlist.m3u",
            },
        )(),
    )
    monkeypatch.setattr(
        controller_modes_app,
        "replace_playlist_from_file",
        lambda port, password, playlist_path, repeat_mode="": calls.append((port, password, Path(playlist_path), repeat_mode)) or True,
    )

    code = controller_modes_app.main([
        "apply-fmode",
        "--primary-sources",
        "primary",
        "--portrait-sources",
        "portrait",
        "--landscape-sources",
        "landscape",
        "--favs-file",
        str(tmp_path / "favs.csv"),
        "--state-dir",
        str(tmp_path / "state"),
        "--enabled",
        "1",
        "--primary-port",
        "8090",
        "--portrait-port",
        "8091",
        "--landscape-port",
        "8092",
        "--password",
        "pw",
        "--result-file",
        str(result_file),
    ])

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")

    assert code == 0
    assert calls == [
        (8090, "pw", tmp_path / "primary_vlc_playlist.m3u", ""),
        (8091, "pw", tmp_path / "portrait_vlc_playlist.m3u", "all"),
        (8092, "pw", tmp_path / "landscape_vlc_playlist.m3u", "all"),
    ]
    assert parser.get("result", "next_locked2") == "0"
    assert parser.get("result", "next_locked3") == "0"
