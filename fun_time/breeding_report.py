from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app_support import watch_weights
from app_support.kino_folder import is_ai_video

from .config import load_config
from .gallery import Gallery, the_librarys_gallery
from .media_metadata import recorded_for
from .modes import collect_video_files


@dataclass(frozen=True)
class BreedingRow:
    path: str
    weight: float
    completions: int
    skips: int
    locks: int
    orientation: str
    action: str
    seed: str
    prompt: str


def _norm_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _orientation_of(path: str) -> str:
    if "\\portrait\\" in path or "/portrait/" in path:
        return "P"
    if "\\landscape\\" in path or "/landscape/" in path:
        return "L"
    return "?"


def _identity_of(metadata: dict) -> tuple[str, str, str]:
    video = metadata.get("video") or {}
    source = metadata.get("source_image") or {}
    action = _norm_text(video.get("action"))
    if source:
        return action, _norm_text(source.get("seed")), _norm_text(source.get("positive_prompt"))
    return action, _norm_text(video.get("seed")), _norm_text(video.get("prompt"))


def _count(block: dict, field: str) -> int:
    try:
        return int(block.get(field, 0) or 0)
    except (TypeError, ValueError):
        return 0


def build_breeding_rows(
    video_paths: Iterable[str],
    metadata_root: str | Path | None,
    gallery: Gallery,
) -> list[BreedingRow]:
    rows = [row for path in video_paths
            if (row := _ranked(path, _record_of(path, metadata_root, gallery))) is not None]
    rows.sort(key=lambda row: (-row.weight, -(row.completions + row.locks), row.skips, row.path))
    return rows


def _record_of(path: str, metadata_root: str | Path | None, gallery: Gallery) -> dict:
    if not is_ai_video(path):
        return recorded_for(path, metadata_root)
    record = gallery.record_of(path)
    counts = record.get(watch_weights.WATCH_BLOCK, {})
    never_watched = not any(counts.get(field) for field in watch_weights.COUNT_FIELDS)
    return {} if never_watched else record


def _ranked(path: str, metadata: dict) -> BreedingRow | None:
    block = metadata.get(watch_weights.WATCH_BLOCK)
    if not isinstance(block, dict):
        return None
    action, seed, prompt = _identity_of(metadata)
    return BreedingRow(
        path=path,
        weight=watch_weights.weight_of(metadata),
        completions=_count(block, "completions"),
        skips=_count(block, "skips"),
        locks=_count(block, "locks"),
        orientation=_orientation_of(path),
        action=action,
        seed=seed,
        prompt=prompt,
    )


_PROMPT_WIDTH = 42


# ASCII-only output: Windows consoles on legacy codepages mangle em-dashes
# and ellipses, and this report exists to be read in one.
def _video(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def _format_row(rank: int, row: BreedingRow) -> str:
    return (
        f"{rank:>3}  x{row.weight:.2f}  "
        f"{row.completions:>3} {row.locks:>3} {row.skips:>3}  "
        f"{row.orientation}  "
        f"{_video(row.action, 16):<16}  "
        f"{_video(row.seed, 12):<12}  "
        f"{_video(row.prompt, _PROMPT_WIDTH):<{_PROMPT_WIDTH}}  "
        f"{Path(row.path).name}"
    )


_HEADER = (
    "  #  WEIGHT   C   L   S  O  "
    f"{'ACTION':<16}  {'SEED':<12}  {'PROMPT':<{_PROMPT_WIDTH}}  FILE"
)


def _section(title: str, rows: list[BreedingRow], top: int) -> list[str]:
    lines = [f"{title}:", _HEADER]
    for rank, row in enumerate(rows[:top], start=1):
        lines.append(_format_row(rank, row))
    if len(rows) > top:
        lines.append(f"     ... and {len(rows) - top} more")
    lines.append("")
    return lines


def render_breeding_report(rows: list[BreedingRow], *, top: int) -> str:
    if not rows:
        return "No watch stats recorded yet - play something on the satellites first."
    rising = [row for row in rows if row.weight >= 1.0]
    fading = sorted(
        (row for row in rows if row.weight < 1.0),
        key=lambda row: (row.weight, -row.skips, row.path),
    )
    lines = [
        (f"Breeding leaderboard - {len(rows)} clips tracked "
        f"({len(rising)} rising, {len(fading)} fading) | "
        "C=completions  L=locks  S=skips  O=orientation (P/L)"),
        "",
    ]
    if rising:
        lines.extend(_section("Rising", rising, top))
    if fading:
        lines.extend(_section("Fading (most-skipped first)", fading, top))
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None, *, gallery: Gallery | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Watch leaderboard (breeding state). Columns: WEIGHT = the "
            "shuffle-frequency multiplier, C = completions (full watches, one "
            "per repeat loop while locked), L = locks, S = skips, "
            "O = orientation (P portrait / L landscape)."
        )
    )
    parser.add_argument("--top", type=int, default=15, help="rows per section (default 15)")
    parser.add_argument("--all", action="store_true", help="show every tracked clip")
    parser.add_argument("--config", default=None, help="alternate fun_time_config.json")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    paths = config.paths
    sources = "|".join(
        str(folder)
        for folder in (*paths.portrait_dirs, *paths.landscape_dirs, *paths.main_funestra_library_dirs)
    )
    rows = build_breeding_rows(collect_video_files(sources), config.regen.metadata_root,
                               the_librarys_gallery() if gallery is None else gallery)
    print(render_breeding_report(rows, top=len(rows) if args.all else args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
