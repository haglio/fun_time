from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .windows_bridge_startup import (
    launch_ui_companions,
    prepare_random_favs_browser_manifest,
    start_core_session,
)


def _read_manifest(path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time Windows bridge startup helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    core_session = subparsers.add_parser(
        "start-core-session",
        help="Run the broker/state/browser/core-media startup sequence and write the core PIDs.",
    )
    core_session.add_argument("--manifest", required=True)
    core_session.add_argument("--result-file", required=True)

    browser = subparsers.add_parser(
        "prepare-random-favs-browser-manifest",
        help="Write the Random Favs Browser manifest.",
    )
    browser.add_argument("--config", required=True)
    browser.add_argument("--output", required=True)

    ui = subparsers.add_parser(
        "launch-ui-companions",
        help="Launch the dashboard, Robot Hand, and audio companion processes.",
    )
    ui.add_argument("--manifest", required=True)
    ui.add_argument("--dashboard-x", type=int, required=True)
    ui.add_argument("--dashboard-y", type=int, required=True)
    ui.add_argument("--dashboard-width", type=int, required=True)
    ui.add_argument("--dashboard-height", type=int, required=True)
    ui.add_argument("--mfp-pid", type=int, required=True)
    ui.add_argument("--robot-x", type=int, required=True)
    ui.add_argument("--robot-y", type=int, required=True)
    ui.add_argument("--robot-width", type=int, required=True)
    ui.add_argument("--robot-height", type=int, required=True)
    ui.add_argument("--result-file", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "prepare-random-favs-browser-manifest":
        prepare_random_favs_browser_manifest(args.config, args.output)
        return 0

    m = _read_manifest(args.manifest)

    if args.command == "start-core-session":
        start_core_session(
            project_dir=m["runtime"]["project_dir"],
            config_path=m["runtime"]["config_path"],
            random_favs_browser_manifest_file=m["random_favs_browser"]["manifest_file"],
            enabled_file=m["commands"]["robot_hand_enabled_file"],
            paused_file=m["commands"]["robot_hand_paused_file"],
            audio_paused_file=m["commands"]["audio_paused_file"],
            vlc_exe=m["executables"]["vlc_exe"],
            mfp_exe=m["executables"]["mfp_exe"],
            primary_sources=m["media"]["primary_vlc_sources"],
            portrait_sources=m["media"]["portrait_dirs"],
            landscape_sources=m["media"]["landscape_dirs"],
            primary_port=int(m["controller"]["primary_vlc_port"]),
            portrait_port=int(m["controller"]["vlc2_port"]),
            landscape_port=int(m["controller"]["vlc3_port"]),
            password=m["controller"]["vlc_pass"],
            result_file=args.result_file,
        )
        return 0

    if args.command == "launch-ui-companions":
        launch_ui_companions(
            python_exe=m["executables"]["python_exe"],
            dashboard_module=m["modules"]["dashboard_module"],
            dashboard_enabled=m["dashboard"]["enabled"].strip() not in {"", "0", "false", "False"},
            windows_bridge_manifest_path=args.manifest,
            dashboard_x=args.dashboard_x,
            dashboard_y=args.dashboard_y,
            dashboard_width=args.dashboard_width,
            dashboard_height=args.dashboard_height,
            mfp_pid=args.mfp_pid,
            robot_hand_module=m["modules"]["robot_hand_module"],
            audio_module=m["modules"]["audio_module"],
            config_path=m["runtime"]["config_path"],
            clips_folder=m["media"]["robot_hand_clips"],
            audio_folder=m["media"]["robot_hand_audio"],
            robot_x=args.robot_x,
            robot_y=args.robot_y,
            robot_width=args.robot_width,
            robot_height=args.robot_height,
            result_file=args.result_file,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
