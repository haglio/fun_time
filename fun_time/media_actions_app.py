from __future__ import annotations

import argparse
from pathlib import Path

from .media_actions import run_media_action


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time media file actions.")
    parser.add_argument("action", choices=("ensure-in-favs", "remove-from-favs", "move-to-weird"))
    parser.add_argument("--favs-file", required=True)
    parser.add_argument("--weird-dir", required=True)
    parser.add_argument("--path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_media_action(
        args.action,
        favs_file=Path(args.favs_file),
        weird_dir=Path(args.weird_dir),
        path=args.path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
