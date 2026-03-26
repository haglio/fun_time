from __future__ import annotations

import argparse
import configparser
from pathlib import Path

from .config import LayoutConfig
from .windows_bridge_window_layout import (
    MonitorRect,
    Size,
    compute_window_layout,
    write_window_layout_plan,
)


def _read_manifest(path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Fun Time window layout planning actions.")
    parser.add_argument("action", choices=("write-plan",))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--main-x", required=True, type=int)
    parser.add_argument("--main-y", required=True, type=int)
    parser.add_argument("--main-width", required=True, type=int)
    parser.add_argument("--main-height", required=True, type=int)
    parser.add_argument("--secondary-x", required=True, type=int)
    parser.add_argument("--secondary-y", required=True, type=int)
    parser.add_argument("--secondary-width", required=True, type=int)
    parser.add_argument("--secondary-height", required=True, type=int)
    parser.add_argument("--mfp-width", required=True, type=int)
    parser.add_argument("--mfp-height", required=True, type=int)
    parser.add_argument("--plan-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action != "write-plan":
        raise ValueError(f"Unsupported action: {args.action}")

    m = _read_manifest(args.manifest)

    layout = LayoutConfig(
        main_monitor=1,
        secondary_monitor=2,
        primary_top_ratio=float(m["layout"]["primary_top_ratio"]),
        landscape_width_ratio=float(m["layout"]["landscape_width_ratio"]),
        mfp_width_ratio=float(m["layout"]["mfp_width_ratio"]),
        mfp_height_ratio=float(m["layout"]["mfp_height_ratio"]),
    )
    plan = compute_window_layout(
        main_monitor=MonitorRect(args.main_x, args.main_y, args.main_width, args.main_height),
        secondary_monitor=MonitorRect(args.secondary_x, args.secondary_y, args.secondary_width, args.secondary_height),
        layout_config=layout,
        mfp_size=Size(args.mfp_width, args.mfp_height),
    )
    write_window_layout_plan(Path(args.plan_file), plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
