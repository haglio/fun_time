from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .windows_bridge_runtime_flow import (
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_sync_robot_hand,
    apply_toggle_fmode,
    apply_toggle_robot_hand_enabled,
    build_omnipause_toggle,
)


def _to_bool(value: str) -> bool:
    return value.strip() not in {"", "0", "false", "False"}


def _write_result_file(result_file: str, values: dict[str, str]) -> None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = values
    target = Path(result_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run consolidated Fun Time runtime flow actions.")
    parser.add_argument(
        "action",
        choices=(
            "sync-robot-hand",
            "toggle-robot-hand-enabled",
            "toggle-fmode",
            "build-omnipause-toggle",
            "apply-enter-omnipause",
            "apply-leave-omnipause",
        ),
    )
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--robot-hand-mode-on", default="0")
    parser.add_argument("--omni-paused", default="0")
    parser.add_argument("--f-mode-enabled", default="0")
    parser.add_argument("--skip-primary-resume", default="0")
    parser.add_argument("--enabled-file", default="")
    parser.add_argument("--mode-state-file", default="")
    parser.add_argument("--paused-file", default="")
    parser.add_argument("--audio-paused-file", default="")
    parser.add_argument("--robot-hand-paused-file", default="")
    parser.add_argument("--primary-sources", default="")
    parser.add_argument("--portrait-sources", default="")
    parser.add_argument("--landscape-sources", default="")
    parser.add_argument("--favs-file", default="")
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--primary-port", type=int)
    parser.add_argument("--portrait-port", type=int)
    parser.add_argument("--landscape-port", type=int)
    parser.add_argument("--password", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.action == "sync-robot-hand":
        result = apply_sync_robot_hand(
            robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
            omni_paused=_to_bool(args.omni_paused),
            enabled_file=args.enabled_file,
            mode_state_file=args.mode_state_file,
            paused_file=args.paused_file,
            audio_paused_file=args.audio_paused_file,
            primary_port=args.primary_port or 0,
            password=args.password,
        )
        _write_result_file(
            args.result_file,
            {
                "next_robot_hand_mode": "1" if result.next_robot_hand_mode else "0",
                "current_enabled": "1" if result.current_enabled else "0",
                "enforce_outputs": "1" if result.enforce_outputs else "0",
                "enforce_active": "1" if result.enforce_active else "0",
                "is_transition": "1" if result.is_transition else "0",
                "log_message": result.log_message,
            },
        )
        return 0

    if args.action == "toggle-robot-hand-enabled":
        result = apply_toggle_robot_hand_enabled(
            robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
            omni_paused=_to_bool(args.omni_paused),
            enabled_file=args.enabled_file,
            mode_state_file=args.mode_state_file,
            paused_file=args.paused_file,
            audio_paused_file=args.audio_paused_file,
            primary_port=args.primary_port or 0,
            password=args.password,
        )
        _write_result_file(
            args.result_file,
            {
                "next_robot_hand_mode": "1" if result.next_robot_hand_mode else "0",
                "current_enabled": "1" if result.current_enabled else "0",
                "enforce_outputs": "1" if result.enforce_outputs else "0",
                "enforce_active": "1" if result.enforce_active else "0",
                "is_transition": "1" if result.is_transition else "0",
                "log_message": result.log_message,
            },
        )
        return 0

    if args.action == "toggle-fmode":
        result = apply_toggle_fmode(
            f_mode_enabled=_to_bool(args.f_mode_enabled),
            primary_sources=args.primary_sources,
            portrait_sources=args.portrait_sources,
            landscape_sources=args.landscape_sources,
            favs_file=args.favs_file,
            state_dir=args.state_dir,
            primary_port=args.primary_port or 0,
            portrait_port=args.portrait_port or 0,
            landscape_port=args.landscape_port or 0,
            password=args.password,
        )
        _write_result_file(
            args.result_file,
            {
                "success": "1" if result.success else "0",
                "next_f_mode_enabled": "1" if result.next_f_mode_enabled else "0",
                "next_locked2": "1" if result.next_locked2 else "0",
                "next_locked3": "1" if result.next_locked3 else "0",
                "log_message": result.log_message,
            },
        )
        return 0

    if args.action == "build-omnipause-toggle":
        result = build_omnipause_toggle(
            omni_paused=_to_bool(args.omni_paused),
            robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
        )
        _write_result_file(
            args.result_file,
            {
                "action": result.action,
                "next_omni_paused": "1" if result.next_omni_paused else "0",
                "robot_hand_branch": "1" if result.robot_hand_branch else "0",
                "log_message": result.log_message,
            },
        )
        return 0

    if args.action == "apply-enter-omnipause":
        result = apply_enter_omnipause(
            omni_paused=_to_bool(args.omni_paused),
            robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
            portrait_port=args.portrait_port or 0,
            landscape_port=args.landscape_port or 0,
            primary_port=args.primary_port or 0,
            password=args.password,
            robot_hand_paused_file=args.robot_hand_paused_file,
            audio_paused_file=args.audio_paused_file,
        )
        _write_result_file(
            args.result_file,
            {
                "action": result.action,
                "next_omni_paused": "1" if result.next_omni_paused else "0",
                "robot_hand_branch": "1" if result.robot_hand_branch else "0",
                "log_message": result.log_message,
            },
        )
        return 0

    if args.action == "apply-leave-omnipause":
        result = apply_leave_omnipause(
            omni_paused=_to_bool(args.omni_paused),
            robot_hand_mode_on=_to_bool(args.robot_hand_mode_on),
            skip_primary_resume=_to_bool(args.skip_primary_resume),
            primary_port=args.primary_port or 0,
            portrait_port=args.portrait_port or 0,
            landscape_port=args.landscape_port or 0,
            password=args.password,
            robot_hand_paused_file=args.robot_hand_paused_file,
            audio_paused_file=args.audio_paused_file,
        )
        _write_result_file(
            args.result_file,
            {
                "action": result.action,
                "next_omni_paused": "1" if result.next_omni_paused else "0",
                "robot_hand_branch": "1" if result.robot_hand_branch else "0",
                "log_message": result.log_message,
            },
        )
        return 0

    raise ValueError(f"Unsupported action: {args.action}")


if __name__ == "__main__":
    raise SystemExit(main())
