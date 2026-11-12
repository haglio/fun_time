from __future__ import annotations

import json
from pathlib import Path

from app_support import kino_folder
from app_support.watch_weights import weight_for

from fun_time.breeding_report import BreedingRow, build_breeding_rows, main, render_breeding_report
from fun_time.gallery import Gallery
from fun_time.media_metadata import metadata_path_for
from tests.made_up_gallery import a_row, made_up_origenerator


def _library(tmp_path: Path, names: list[str]) -> tuple[Path, dict[str, str]]:
    media_root = tmp_path / "videos" / "videos"
    paths: dict[str, str] = {}
    for name in names:
        orientation = "portrait" if not name.startswith("wide_") else "landscape"
        video = kino_folder.upscales_in(media_root / "kino") / orientation / "provider" / f"{name}_topaz.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
    return media_root, paths


def _watched(name: str, completions: int, skips: int, locks: int, *, seed: int | None = None,
             prompt: str | None = None) -> dict:
    return a_row(f"gen {name}", f"{name}.mp4", watch_completions=completions, watch_skips=skips,
                 watch_locks=locks, seed=seed, positive_prompt=prompt)


def test_build_breeding_rows_ranks_ai_videos_by_the_weight_their_gallery_counts_make(tmp_path: Path):
    _media_root, paths = _library(tmp_path, ["loved", "wide_meh", "hated_bare", "unwatched", "no_row"])
    gallery = Gallery([
        _watched("loved", 6, 0, 1, seed=111, prompt="two cute dolls,  rainbow bedroom"),
        _watched("wide_meh", 1, 1, 0, seed=222),
        _watched("hated_bare", 0, 9, 0),
        a_row("gen unwatched", "unwatched.mp4", seed=333),
    ])

    rows = build_breeding_rows(paths.values(), None, gallery)

    assert [row.path for row in rows] == [paths["loved"], paths["wide_meh"], paths["hated_bare"]]
    assert rows[0] == BreedingRow(
        path=paths["loved"], weight=weight_for({"completions": 6, "skips": 0, "locks": 1}),
        completions=6, skips=0, locks=1, orientation="P", action="", seed="111",
        prompt="two cute dolls, rainbow bedroom")
    assert rows[1].orientation == "L"
    assert (rows[-1].seed, rows[-1].prompt) == ("", "")


def test_an_ai_videos_own_record_is_never_read_for_the_leaderboard(tmp_path: Path):
    _media_root, paths = _library(tmp_path, ["loved"])
    metadata_root = tmp_path / "videos" / "metadata"
    record = metadata_path_for(paths["loved"], metadata_root)
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"watch": {"completions": 6, "weight": 8.0}}), encoding="utf-8")

    assert build_breeding_rows(paths.values(), metadata_root, Gallery([])) == []


def test_a_video_outside_the_ai_folders_is_ranked_by_its_own_record(tmp_path: Path):
    video = tmp_path / "videos" / "videos" / "kino" / "2D" / "non_AI" / "scene one.mp4"
    metadata_root = tmp_path / "videos" / "metadata"
    record = metadata_path_for(video, metadata_root)
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({
        "video": {"action": "Alpha", "seed": "9"},
        "watch": {"completions": 2, "skips": 1, "locks": 0, "weight": 1.5}}), encoding="utf-8")

    assert build_breeding_rows([str(video)], metadata_root, Gallery([])) == [BreedingRow(
        path=str(video), weight=1.5, completions=2, skips=1, locks=0, orientation="?",
        action="Alpha", seed="9", prompt="")]


def test_render_breeding_report_shows_rising_and_fading_sections(tmp_path: Path):
    _media_root, paths = _library(tmp_path, ["loved", "meh", "hated"])
    gallery = Gallery([_watched("loved", 6, 0, 1, seed=111, prompt="p" * 100),
                       _watched("meh", 1, 1, 0), _watched("hated", 0, 9, 0)])
    rows = build_breeding_rows(paths.values(), None, gallery)

    report = render_breeding_report(rows, top=10)

    assert "3 clips tracked" in report
    first_line = report.splitlines()[0]
    assert "C=completions" in first_line
    assert "L=locks" in first_line
    assert "S=skips" in first_line
    assert "O=orientation (P/L)" in first_line
    assert "Rising" in report
    assert "Fading" in report
    lines = report.splitlines()
    loved_line = next(line for line in lines if "loved_topaz.mp4" in line)
    assert f"x{rows[0].weight:.2f}" in loved_line
    assert "111" in loved_line
    assert "p" * 100 not in report, "long prompts must be truncated"
    hated_line = next(line for line in lines if "hated_topaz.mp4" in line)
    assert lines.index(hated_line) > lines.index(loved_line)


def test_render_breeding_report_limits_rows_and_handles_empty():
    rows = sorted(
        (
            BreedingRow(
                path=f"c:\\clips\\clip{i:02}.mp4", weight=1.0 + i, completions=i, skips=0,
                locks=0, orientation="?", action="", seed="", prompt="",
            )
            for i in range(20)
        ),
        key=lambda row: -row.weight,
    )

    limited = render_breeding_report(rows, top=5)

    assert limited.count(".mp4") == 5, "only the top N rise to the leaderboard"
    assert "and 15 more" in limited
    assert "No watch stats recorded yet" in render_breeding_report([], top=5)


def test_main_prints_the_leaderboard_for_the_configured_library(cfg_factory, tmp_path, capsys):
    _media_root, _paths = _library(tmp_path, ["loved", "unwatched"])
    gallery = Gallery([_watched("loved", 0, 0, 1), a_row("gen unwatched", "unwatched.mp4")])

    exit_code = main(["--config", str(cfg_factory()), "--top", "3"], gallery=gallery)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "1 clips tracked" in out
    assert "loved_topaz.mp4" in out
    assert "unwatched_topaz.mp4" not in out


def test_main_reads_the_librarys_gallery_when_handed_none(cfg_factory, tmp_path, capsys, monkeypatch):
    _library(tmp_path, ["loved"])
    origenerator = made_up_origenerator(tmp_path, _watched("loved", 0, 0, 1))
    monkeypatch.setattr("fun_time.gallery.the_librarys_origenerator", lambda: origenerator)

    main(["--config", str(cfg_factory())])

    assert "loved_topaz.mp4" in capsys.readouterr().out
