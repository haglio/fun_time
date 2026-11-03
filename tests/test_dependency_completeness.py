from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_check

MERGE_GATE = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "merge-gate.yml"


def test_the_gate_takes_the_video_engine_from_the_build_funestra_pins():
    gate = MERGE_GATE.read_text(encoding="utf-8")

    assert "tools/fetch_libmpv.py" in gate
    assert "mpv-winbuild" not in gate, "a community feed is named here again"
    assert "gh api" not in gate, "this gate is asking a feed what to install again"
