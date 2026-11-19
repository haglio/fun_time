from __future__ import annotations

from pathlib import Path

MAIN_FUNESTRA = Path(__file__).resolve().parents[1] / "main_funestra"


def test_the_pin_that_carries_the_identity_stays_fun_times_to_stamp():
    stampers = [
        module.name for module in sorted(MAIN_FUNESTRA.glob("*.py"))
        if "stamp_pinned_shortcuts" in module.read_text(encoding="utf-8")
    ]

    assert not stampers, f"{stampers} stamp the pinned shortcut, which fun_time/orchestrator.py keeps"
