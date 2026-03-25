from __future__ import annotations

import argparse
from pathlib import Path

from .controller_modes import build_fmode_playlists


EMPTY_PLAYLIST_EXIT_CODE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time controller mode actions.")
    parser.add_argument("action", choices=("write-fmode-playlists",))
    parser.add_argument("--primary-sources", required=True)
    parser.add_argument("--portrait-sources", required=True)
    parser.add_argument("--landscape-sources", required=True)
    parser.add_argument("--favs-file", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--enabled", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action != "write-fmode-playlists":
        raise ValueError(f"Unsupported action: {args.action}")

    plan = build_fmode_playlists(
        primary_sources=args.primary_sources,
        portrait_sources=args.portrait_sources,
        landscape_sources=args.landscape_sources,
        favs_file=Path(args.favs_file),
        state_dir=Path(args.state_dir),
        enabled=args.enabled.strip() not in {"", "0", "false", "False"},
    )
    return 0 if plan.success else EMPTY_PLAYLIST_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
