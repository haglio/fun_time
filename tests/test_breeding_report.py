from __future__ import annotations

import json
from pathlib import Path

from fun_time.breeding_report import build_breeding_rows, render_breeding_report
from fun_time.media_metadata import metadata_path_for, normalize_path_key


def _library(tmp_path: Path, videos: dict[str, dict | None]) -> tuple[Path, Path, dict[str, str]]:
    media_root = tmp_path / "media"
    metadata_root = tmp_path / "metadata"
    paths: dict[str, str] = {}
    for name, meta in videos.items():
        orientation = "portrait" if not name.startswith("wide_") else "landscape"
        video = media_root / orientation / "provider" / f"{name}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_text("x", encoding="utf-8")
        paths[name] = str(video)
        if meta is not None:
            sidecar = metadata_path_for(video, media_root, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(meta), encoding="utf-8")
    return media_root, metadata_root, paths


def _meta(action: str, image_seed: str, prompt: str = "two cute dolls,  rainbow bedroom") -> dict:
    return {
        "video": {"prompt": f"do {action}", "action": action, "seed": "9"},
        "source_image": {"positive_prompt": prompt, "seed": image_seed},
    }


def _stats(**per_name_counts) -> dict[str, dict[str, int]]:
    return {
        name: {"completions": c, "skips": s, "locks": l}
        for name, (c, s, l) in per_name_counts.items()
    }


def test_build_breeding_rows_ranks_by_weight_with_metadata_identity(tmp_path: Path):
    media_root, metadata_root, paths = _library(tmp_path, {
        "loved": _meta("Alpha", "111"),
        "wide_meh": _meta("Dancing", "222"),
        "hated_no_meta": None,
    })
    stats = {
        normalize_path_key(paths["loved"]): {"completions": 6, "skips": 0, "locks": 1},
        normalize_path_key(paths["wide_meh"]): {"completions": 1, "skips": 1, "locks": 0},
        normalize_path_key(paths["hated_no_meta"]): {"completions": 0, "skips": 9, "locks": 0},
    }

    rows = build_breeding_rows(stats, media_root, metadata_root)

    assert [row.weight for row in rows] == sorted((row.weight for row in rows), reverse=True)
    top = rows[0]
    assert top.path == normalize_path_key(paths["loved"])
    assert top.completions == 6 and top.locks == 1 and top.skips == 0
    assert top.orientation == "P"
    assert top.action == "Alpha"
    assert top.seed == "111"
    assert top.prompt == "two cute dolls, rainbow bedroom"
    assert rows[1].orientation == "L"
    bottom = rows[-1]
    assert bottom.path == normalize_path_key(paths["hated_no_meta"])
    assert bottom.action == "" and bottom.seed == "" and bottom.prompt == ""


def test_render_breeding_report_shows_rising_and_fading_sections(tmp_path: Path):
    media_root, metadata_root, paths = _library(tmp_path, {
        "loved": _meta("Alpha", "111", prompt="p" * 100),
        "meh": _meta("Dancing", "222"),
        "hated": _meta("Twerk", "333"),
    })
    stats = {
        normalize_path_key(paths["loved"]): {"completions": 6, "skips": 0, "locks": 1},
        normalize_path_key(paths["meh"]): {"completions": 1, "skips": 1, "locks": 0},
        normalize_path_key(paths["hated"]): {"completions": 0, "skips": 9, "locks": 0},
    }
    rows = build_breeding_rows(stats, media_root, metadata_root)

    report = render_breeding_report(rows, top=10)

    assert "3 clips tracked" in report
    assert "Rising" in report and "Fading" in report
    lines = report.splitlines()
    loved_line = next(line for line in lines if "loved.mp4" in line)
    assert "x8.00" in loved_line and "Alpha" in loved_line and "111" in loved_line
    assert "p" * 100 not in report, "long prompts must be truncated"
    hated_line = next(line for line in lines if "hated.mp4" in line)
    assert "x0.12" in hated_line
    # The fading section lists the most-skipped first.
    assert lines.index(hated_line) > lines.index(loved_line)


def test_render_breeding_report_limits_rows_and_handles_empty():
    rows = build_breeding_rows(
        {f"c:\\clips\\clip{i:02}.mp4": {"completions": i, "skips": 0, "locks": 0} for i in range(20)},
        None,
        None,
    )

    limited = render_breeding_report(rows, top=5)

    assert limited.count(".mp4") == 5, "only the top N rise to the leaderboard"
    assert "and 15 more" in limited
    assert "No watch stats recorded yet" in render_breeding_report([], top=5)


def test_main_prints_the_leaderboard_for_the_configured_state(cfg_factory, tmp_path, capsys):
    from fun_time.breeding_report import main
    from fun_time.watch_stats import record_watch_event

    media_root, metadata_root, paths = _library(tmp_path, {
        "loved": _meta("Alpha", "111"),
    })
    config_path = cfg_factory({
        "provider_regen": {"media_root": str(media_root), "metadata_root": str(metadata_root)},
    })
    record_watch_event(tmp_path / "state" / "watch_stats.json", paths["loved"], "lock")

    exit_code = main(["--config", str(config_path), "--top", "3"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "1 clips tracked" in out
    assert "loved.mp4" in out and "Alpha" in out
