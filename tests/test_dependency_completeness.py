"""The merge gate takes the video engine from the build funestra pins. The
checks that sat beside this one, that what the repo needs is what its pyproject
says, are app_support's gates, which every repo names once in its pytest options.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_check

MERGE_GATE = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "merge-gate.yml"


def test_the_gate_takes_the_video_engine_from_the_build_funestra_pins():
    """The engine is ~117 MB, ships in no package, and every Funestra stands on it.

    This gate used to ask a community repository's releases feed for the newest
    asset matching a glob and copy the result next to the interpreter -- no
    version, no checksum, no signature, in a job holding the workflow token --
    with a fallback to a second, unrelated publisher.  ``libmpv.lock`` in
    funestra names the build and its digest and refuses a mismatch, and this
    repo already runs that fetcher's twin at startup; the gate is the last place
    that was still taking whatever the feed served that morning.
    """
    gate = MERGE_GATE.read_text(encoding="utf-8")

    assert "tools/fetch_libmpv.py" in gate
    assert "mpv-winbuild" not in gate, "a community feed is named here again"
    assert "gh api" not in gate, "this gate is asking a feed what to install again"
