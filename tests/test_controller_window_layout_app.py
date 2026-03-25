from __future__ import annotations

from pathlib import Path

from fun_time.controller_window_layout_app import build_parser, main


def test_build_parser_accepts_window_layout_arguments():
    args = build_parser().parse_args([
        "write-plan",
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
        "--primary-top-ratio",
        "0.7272727273",
        "--landscape-width-ratio",
        "0.6666666667",
        "--mfp-width-ratio",
        "0.9",
        "--mfp-height-ratio",
        "0.6",
        "--mfp-width",
        "240",
        "--mfp-height",
        "395",
        "--plan-file",
        "plan.ini",
    ])

    assert args.action == "write-plan"
    assert args.main_width == 2560
    assert args.plan_file == "plan.ini"


def test_main_writes_window_layout_plan_file(tmp_path: Path):
    plan_file = tmp_path / "window_layout_plan.ini"

    code = main([
        "write-plan",
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
        "--primary-top-ratio",
        "0.7272727273",
        "--landscape-width-ratio",
        "0.6666666667",
        "--mfp-width-ratio",
        "0.9",
        "--mfp-height-ratio",
        "0.6",
        "--mfp-width",
        "240",
        "--mfp-height",
        "395",
        "--plan-file",
        str(plan_file),
    ])

    assert code == 0
    assert plan_file.exists()
    assert "[chrome]" in plan_file.read_text(encoding="utf-8")
