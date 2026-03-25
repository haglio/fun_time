from __future__ import annotations

import argparse

from .windows_bridge_dashboard_bridge import write_dashboard_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write Fun Time dashboard bridge state.")
    parser.add_argument("--output-file", required=True)
    parser.add_argument("--f-mode-enabled", required=True)
    parser.add_argument("--robot-link-enabled", required=True)
    parser.add_argument("--osr2-mode", required=True, choices=("auto", "controlled"))
    parser.add_argument("--mfp-alive", required=True)
    parser.add_argument("--primary-uses-robot-hand", required=True)
    parser.add_argument("--portrait-locked", required=True)
    parser.add_argument("--landscape-locked", required=True)
    return parser


def _to_bool(value: str) -> bool:
    return value.strip() not in {"", "0", "false", "False"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    write_dashboard_snapshot(
        args.output_file,
        f_mode_enabled=_to_bool(args.f_mode_enabled),
        robot_link_enabled=_to_bool(args.robot_link_enabled),
        osr2_mode=args.osr2_mode,
        mfp_alive=_to_bool(args.mfp_alive),
        primary_uses_robot_hand=_to_bool(args.primary_uses_robot_hand),
        portrait_locked=_to_bool(args.portrait_locked),
        landscape_locked=_to_bool(args.landscape_locked),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
