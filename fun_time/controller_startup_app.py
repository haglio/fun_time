from __future__ import annotations

import argparse

from .controller_startup import (
    launch_core_apps,
    launch_ui_companions,
    prepare_random_favs_browser_manifest,
    restart_broker,
    seed_robot_hand_state,
    start_core_session,
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

    core_session = subparsers.add_parser(
        "start-core-session",
        help="Run the broker/state/browser/core-media startup sequence and write the core PIDs.",
    )
    core_session.add_argument("--project-dir", required=True)
    core_session.add_argument("--config", required=True)
    core_session.add_argument("--random-favs-browser-manifest-file", required=True)
    core_session.add_argument("--enabled-file", required=True)
    core_session.add_argument("--paused-file", required=True)
    core_session.add_argument("--audio-paused-file", required=True)
    core_session.add_argument("--vlc-exe", required=True)
    core_session.add_argument("--mfp-exe", required=True)
    core_session.add_argument("--primary-sources", required=True)
    core_session.add_argument("--portrait-sources", required=True)
    core_session.add_argument("--landscape-sources", required=True)
    core_session.add_argument("--primary-port", required=True, type=int)
    core_session.add_argument("--portrait-port", required=True, type=int)
    core_session.add_argument("--landscape-port", required=True, type=int)
    core_session.add_argument("--password", required=True)
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
    ui.add_argument("--python-exe", required=True)
    ui.add_argument("--dashboard-module", required=True)
    ui.add_argument("--dashboard-enabled", required=True)
    ui.add_argument("--controller-manifest-path", required=True)
    ui.add_argument("--dashboard-x", required=True, type=int)
    ui.add_argument("--dashboard-y", required=True, type=int)
    ui.add_argument("--dashboard-width", required=True, type=int)
    ui.add_argument("--dashboard-height", required=True, type=int)
    ui.add_argument("--mfp-pid", required=True, type=int)
    ui.add_argument("--robot-hand-module", required=True)
    ui.add_argument("--audio-module", required=True)
    ui.add_argument("--config", required=True)
    ui.add_argument("--clips-folder", required=True)
    ui.add_argument("--audio-folder", required=True)
    ui.add_argument("--robot-x", required=True, type=int)
    ui.add_argument("--robot-y", required=True, type=int)
    ui.add_argument("--robot-width", required=True, type=int)
    ui.add_argument("--robot-height", required=True, type=int)
    ui.add_argument("--result-file", required=True)

    core = subparsers.add_parser(
        "launch-core-apps",
        help="Launch the core VLC and MFP stack and seed the initial VLC state.",
    )
    core.add_argument("--project-dir", required=True)
    core.add_argument("--vlc-exe", required=True)
    core.add_argument("--mfp-exe", required=True)
    core.add_argument("--primary-sources", required=True)
    core.add_argument("--portrait-sources", required=True)
    core.add_argument("--landscape-sources", required=True)
    core.add_argument("--primary-port", required=True, type=int)
    core.add_argument("--portrait-port", required=True, type=int)
    core.add_argument("--landscape-port", required=True, type=int)
    core.add_argument("--password", required=True)
    core.add_argument("--result-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "restart-broker":
        restart_broker(args.project_dir)
        return 0
    if args.command == "seed-robot-hand-state":
        seed_robot_hand_state(args.enabled_file, args.paused_file, args.audio_paused_file)
        return 0
    if args.command == "start-core-session":
        start_core_session(
            project_dir=args.project_dir,
            config_path=args.config,
            random_favs_browser_manifest_file=args.random_favs_browser_manifest_file,
            enabled_file=args.enabled_file,
            paused_file=args.paused_file,
            audio_paused_file=args.audio_paused_file,
            vlc_exe=args.vlc_exe,
            mfp_exe=args.mfp_exe,
            primary_sources=args.primary_sources,
            portrait_sources=args.portrait_sources,
            landscape_sources=args.landscape_sources,
            primary_port=args.primary_port,
            portrait_port=args.portrait_port,
            landscape_port=args.landscape_port,
            password=args.password,
            result_file=args.result_file,
        )
        return 0
    if args.command == "launch-ui-companions":
        launch_ui_companions(
            python_exe=args.python_exe,
            dashboard_module=args.dashboard_module,
            dashboard_enabled=args.dashboard_enabled.strip() not in {"", "0", "false", "False"},
            controller_manifest_path=args.controller_manifest_path,
            dashboard_x=args.dashboard_x,
            dashboard_y=args.dashboard_y,
            dashboard_width=args.dashboard_width,
            dashboard_height=args.dashboard_height,
            mfp_pid=args.mfp_pid,
            robot_hand_module=args.robot_hand_module,
            audio_module=args.audio_module,
            config_path=args.config,
            clips_folder=args.clips_folder,
            audio_folder=args.audio_folder,
            robot_x=args.robot_x,
            robot_y=args.robot_y,
            robot_width=args.robot_width,
            robot_height=args.robot_height,
            result_file=args.result_file,
        )
        return 0
    if args.command == "launch-core-apps":
        launch_core_apps(
            project_dir=args.project_dir,
            vlc_exe=args.vlc_exe,
            mfp_exe=args.mfp_exe,
            primary_sources=args.primary_sources,
            portrait_sources=args.portrait_sources,
            landscape_sources=args.landscape_sources,
            primary_port=args.primary_port,
            portrait_port=args.portrait_port,
            landscape_port=args.landscape_port,
            password=args.password,
            result_file=args.result_file,
        )
        return 0

    prepare_random_favs_browser_manifest(args.config, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
