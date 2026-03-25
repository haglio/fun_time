from __future__ import annotations

import argparse
from pathlib import Path

from .controller_vlc_actions import (
    ensure_playback_state,
    get_current_file_path,
    replace_playlist_from_file,
    set_repeat_mode,
    wait_for_http,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time VLC control actions.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    repeat_parser = subparsers.add_parser("set-repeat-mode")
    repeat_parser.add_argument("--port", type=int, required=True)
    repeat_parser.add_argument("--password", required=True)
    repeat_parser.add_argument("--target", required=True, choices=("one", "all", "off"))

    playback_parser = subparsers.add_parser("ensure-playback-state")
    playback_parser.add_argument("--port", type=int, required=True)
    playback_parser.add_argument("--password", required=True)
    playback_parser.add_argument("--should-play", required=True)

    playlist_parser = subparsers.add_parser("replace-playlist")
    playlist_parser.add_argument("--port", type=int, required=True)
    playlist_parser.add_argument("--password", required=True)
    playlist_parser.add_argument("--playlist-path", required=True)
    playlist_parser.add_argument("--repeat-mode", default="")

    wait_parser = subparsers.add_parser("wait-for-http")
    wait_parser.add_argument("--port", type=int, required=True)
    wait_parser.add_argument("--password", required=True)
    wait_parser.add_argument("--timeout-ms", type=int, default=5000)

    current_file_parser = subparsers.add_parser("current-file-path")
    current_file_parser.add_argument("--port", type=int, required=True)
    current_file_parser.add_argument("--password", required=True)
    current_file_parser.add_argument("--output-file", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.action == "set-repeat-mode":
        return 0 if set_repeat_mode(args.port, args.password, args.target) else 1

    if args.action == "ensure-playback-state":
        should_play = args.should_play.strip() not in {"", "0", "false", "False"}
        return 0 if ensure_playback_state(args.port, args.password, should_play) else 1

    if args.action == "replace-playlist":
        return 0 if replace_playlist_from_file(
            args.port,
            args.password,
            args.playlist_path,
            repeat_mode=args.repeat_mode,
        ) else 1

    if args.action == "wait-for-http":
        return 0 if wait_for_http(args.port, args.password, args.timeout_ms) else 1

    if args.action == "current-file-path":
        output_path = Path(args.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(get_current_file_path(args.port, args.password), encoding="utf-8")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
