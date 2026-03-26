from __future__ import annotations

from pathlib import Path

from fun_time.windows_bridge_window_layout_app import build_parser, main
from fun_time.windows_bridge_manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.config import load_config


def test_build_parser_accepts_window_layout_arguments():
    args = build_parser().parse_args([
        "write-plan",
        "--manifest",
        "windows_bridge_launch.ini",
        "--main-x",
        "0",
        "--main-y",
        "0",
        "--main-width",
        "2560",
        "--main-height",
        "1392",
        "--secondary-x",
        "2560",
        "--secondary-y",
        "0",
        "--secondary-width",
        "1440",
        "--secondary-height",
        "3440",
        "--mfp-width",
        "240",
        "--mfp-height",
        "395",
        "--plan-file",
        "plan.ini",
    ])

    assert args.action == "write-plan"
    assert args.manifest == "windows_bridge_launch.ini"
    assert args.main_width == 2560
    assert args.plan_file == "plan.ini"


def test_main_reads_layout_ratios_from_manifest(cfg_factory, tmp_path: Path):
    cfg = load_config(cfg_factory())
    manifest_path = write_windows_bridge_manifest(
        cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )
    plan_file = tmp_path / "layout_plan.ini"

    code = main([
        "write-plan",
        "--manifest", str(manifest_path),
        "--main-x", "0",
        "--main-y", "0",
        "--main-width", "2560",
        "--main-height", "1392",
        "--secondary-x", "2560",
        "--secondary-y", "0",
        "--secondary-width", "1440",
        "--secondary-height", "3440",
        "--mfp-width", "240",
        "--mfp-height", "395",
        "--plan-file", str(plan_file),
    ])

    assert code == 0
    assert plan_file.exists()
    assert "[random_favs_browser]" in plan_file.read_text(encoding="utf-8")
