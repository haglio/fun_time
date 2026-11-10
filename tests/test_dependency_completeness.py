from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_check

MERGE_GATE = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "merge-gate.yml"


A_COMMUNITY_FEED_NOBODY_PINS_OR_CHECKS = "mpv-winbuild"
ASKING_A_FEED_FOR_ITS_NEWEST = "gh api"


def test_the_job_holding_the_workflow_token_installs_only_the_engine_funestra_pins():
    gate = MERGE_GATE.read_text(encoding="utf-8")

    assert "tools/fetch_libmpv.py" in gate
    assert A_COMMUNITY_FEED_NOBODY_PINS_OR_CHECKS not in gate, "a community feed is named here again"
    assert ASKING_A_FEED_FOR_ITS_NEWEST not in gate, "this gate is asking a feed what to install again"
