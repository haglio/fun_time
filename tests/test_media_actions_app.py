from __future__ import annotations

from pathlib import Path

from fun_time.media_actions_app import build_parser, main


def test_build_parser_accepts_action_and_paths():
    args = build_parser().parse_args([
        "ensure-in-favs",
        "--favs-file",
        "favs.csv",
        "--weird-dir",
        "weird",
        "--path",
        "clip.mp4",
    ])

    assert args.action == "ensure-in-favs"
    assert args.favs_file == "favs.csv"
    assert args.weird_dir == "weird"
    assert args.path == "clip.mp4"


def test_main_executes_action_and_returns_zero(tmp_path: Path):
    favs = tmp_path / "favs.csv"
    weird_dir = tmp_path / "weird"
    clip = tmp_path / "clip.mp4"
    clip.write_text("x", encoding="utf-8")

    code = main([
        "move-to-weird",
        "--favs-file",
        str(favs),
        "--weird-dir",
        str(weird_dir),
        "--path",
        str(clip),
    ])

    assert code == 0
    assert (weird_dir / "clip.mp4").exists()
