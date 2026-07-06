"""Leaderboard over the watch stats — how the harem is evolving.

Run from the project root:

    ./.venv/Scripts/python.exe -m fun_time.breeding_report [--top N | --all]

Each tracked clip is ranked by its playback weight (the same number the
shuffled satellite builds use), with its generation identity — action, image
seed, prompt — pulled from the metadata sidecars so the ranking reads as a
harem leaderboard rather than a list of opaque filenames.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .config import load_config
from .media_metadata import load_metadata, metadata_path_for
from .watch_stats import load_watch_stats, watch_stats_path, weight_for


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


def _identity_of(
    path: str, media_root: str | Path | None, metadata_root: str | Path | None
) -> tuple[str, str, str]:
    """(action, seed, prompt) from the clip's sidecar, blanks when absent."""
    sidecar = metadata_path_for(path, media_root, metadata_root)
    if sidecar is None or not sidecar.is_file():
        return "", "", ""
    metadata = load_metadata(sidecar)
    video = metadata.get("video") or {}
    source = metadata.get("source_image") or {}
    action = _norm_text(video.get("action"))
    if source:
        return action, _norm_text(source.get("seed")), _norm_text(source.get("positive_prompt"))
    return action, _norm_text(video.get("seed")), _norm_text(video.get("prompt"))


def build_breeding_rows(
    stats: dict[str, dict[str, int]],
    media_root: str | Path | None,
    metadata_root: str | Path | None,
) -> list[BreedingRow]:
    """Every tracked clip as a row, heaviest (most loved) first."""
    rows: list[BreedingRow] = []
    for path, entry in stats.items():
        action, seed, prompt = _identity_of(path, media_root, metadata_root)
        rows.append(BreedingRow(
            path=path,
            weight=weight_for(entry),
            completions=entry.get("completions", 0),
            skips=entry.get("skips", 0),
            locks=entry.get("locks", 0),
            orientation=_orientation_of(path),
            action=action,
            seed=seed,
            prompt=prompt,
        ))
    rows.sort(key=lambda row: (-row.weight, -(row.completions + row.locks), row.skips, row.path))
    return rows


_PROMPT_WIDTH = 42


# ASCII-only output: Windows consoles on legacy codepages mangle em-dashes
# and ellipses, and this report exists to be read in one.
def _clip(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def _format_row(rank: int, row: BreedingRow) -> str:
    return (
        f"{rank:>3}  x{row.weight:.2f}  "
        f"{row.completions:>3} {row.locks:>3} {row.skips:>3}  "
        f"{row.orientation}  "
        f"{_clip(row.action, 16):<16}  "
        f"{_clip(row.seed, 12):<12}  "
        f"{_clip(row.prompt, _PROMPT_WIDTH):<{_PROMPT_WIDTH}}  "
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
    """The leaderboard as monospace text: loved clips first, fading clips last.

    C/L/S = completions / locks / skips; O = orientation (P portrait,
    L landscape). Weight is the same multiplier the shuffled builds use.
    """
    if not rows:
        return "No watch stats recorded yet - play something on the satellites first."
    rising = [row for row in rows if row.weight >= 1.0]
    fading = sorted(
        (row for row in rows if row.weight < 1.0),
        key=lambda row: (row.weight, -row.skips, row.path),
    )
    lines = [
        f"Breeding leaderboard - {len(rows)} clips tracked "
        f"({len(rising)} at or above neutral, {len(fading)} fading)",
        "",
    ]
    if rising:
        lines.extend(_section("Rising", rising, top))
    if fading:
        lines.extend(_section("Fading (most-skipped first)", fading, top))
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch-stats leaderboard (breeding state)")
    parser.add_argument("--top", type=int, default=15, help="rows per section (default 15)")
    parser.add_argument("--all", action="store_true", help="show every tracked clip")
    parser.add_argument("--config", default=None, help="alternate fun_time_config.json")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    stats = load_watch_stats(watch_stats_path(config.paths.state_dir))
    rows = build_breeding_rows(
        stats, config.provider_regen.media_root, config.provider_regen.metadata_root
    )
    print(render_breeding_report(rows, top=len(rows) if args.all else args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
