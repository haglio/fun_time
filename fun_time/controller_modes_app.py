from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .controller_modes import build_fmode_playlists
from .controller_vlc_actions import replace_playlist_from_file


EMPTY_PLAYLIST_EXIT_CODE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time controller mode actions.")
    parser.add_argument("action", choices=("write-fmode-playlists", "apply-fmode"))
    parser.add_argument("--primary-sources", required=True)
    parser.add_argument("--portrait-sources", required=True)
    parser.add_argument("--landscape-sources", required=True)
    parser.add_argument("--favs-file", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--enabled", required=True)
    parser.add_argument("--primary-port", type=int)
    parser.add_argument("--portrait-port", type=int)
    parser.add_argument("--landscape-port", type=int)
    parser.add_argument("--password", default="")
    parser.add_argument("--result-file", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_fmode_playlists(
        primary_sources=args.primary_sources,
        portrait_sources=args.portrait_sources,
        landscape_sources=args.landscape_sources,
        favs_file=Path(args.favs_file),
        state_dir=Path(args.state_dir),
        enabled=args.enabled.strip() not in {"", "0", "false", "False"},
    )
    if not plan.success:
        return EMPTY_PLAYLIST_EXIT_CODE

    if args.action == "write-fmode-playlists":
        return 0
    if args.action != "apply-fmode":
        raise ValueError(f"Unsupported action: {args.action}")
    if not all((args.primary_port, args.portrait_port, args.landscape_port, args.password, args.result_file)):
        raise ValueError("apply-fmode requires ports, password, and result file")

    if not replace_playlist_from_file(args.primary_port, args.password, plan.primary_playlist_path):
        return 4
    if not replace_playlist_from_file(args.portrait_port, args.password, plan.portrait_playlist_path, repeat_mode="all"):
        return 5
    if not replace_playlist_from_file(args.landscape_port, args.password, plan.landscape_playlist_path, repeat_mode="all"):
        return 6

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {
        "next_locked2": "0",
        "next_locked3": "0",
    }
    result_file = Path(args.result_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    with result_file.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
