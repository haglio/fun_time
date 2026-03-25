from __future__ import annotations

import argparse

from .controller_startup import (
    launch_runtime_companions,
    prepare_random_favs_browser_manifest,
    restart_broker,
    seed_robot_hand_state,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time controller startup helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    restart = subparsers.add_parser("restart-broker", help="Restart the broker tray and service.")
    restart.add_argument("--project-dir", required=True)

    seed = subparsers.add_parser(
        "seed-robot-hand-state",
        help="Reset Robot Hand enabled/paused state files at startup.",
    )
    seed.add_argument("--enabled-file", required=True)
    seed.add_argument("--paused-file", required=True)
    seed.add_argument("--audio-paused-file", required=True)

    browser = subparsers.add_parser(
        "prepare-random-favs-browser-manifest",
        help="Write the Random Favs Browser manifest.",
    )
    browser.add_argument("--config", required=True)
    browser.add_argument("--output", required=True)

    companions = subparsers.add_parser(
        "launch-runtime-companions",
        help="Launch the Robot Hand and audio companion processes.",
    )
    companions.add_argument("--python-exe", required=True)
    companions.add_argument("--robot-hand-module", required=True)
    companions.add_argument("--audio-module", required=True)
    companions.add_argument("--config", required=True)
    companions.add_argument("--clips-folder", required=True)
    companions.add_argument("--audio-folder", required=True)
    companions.add_argument("--x", required=True, type=int)
    companions.add_argument("--y", required=True, type=int)
    companions.add_argument("--width", required=True, type=int)
    companions.add_argument("--height", required=True, type=int)
    companions.add_argument("--result-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "restart-broker":
        restart_broker(args.project_dir)
        return 0
    if args.command == "seed-robot-hand-state":
        seed_robot_hand_state(args.enabled_file, args.paused_file, args.audio_paused_file)
        return 0
    if args.command == "launch-runtime-companions":
        launch_runtime_companions(
            python_exe=args.python_exe,
            robot_hand_module=args.robot_hand_module,
            audio_module=args.audio_module,
            config_path=args.config,
            clips_folder=args.clips_folder,
            audio_folder=args.audio_folder,
            x=args.x,
            y=args.y,
            width=args.width,
            height=args.height,
            result_file=args.result_file,
        )
        return 0

    prepare_random_favs_browser_manifest(args.config, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
