"""Property-based tests using hypothesis for fuzz-testing pure functions.

Phase 4D of the cleanup plan. These tests verify that pure parsing and
formatting functions never crash on arbitrary input.
"""
from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from fun_time.content import WebProvider
from fun_time.media_actions import make_web_url_from_path

# Explicit providers keep these independent of the ambient content overlay
# (the real content.local.json is absent on a public checkout).
_PROVIDERS = (
    WebProvider(marker="alpha", gallery_url="https://example.com/alpha/{id}"),
    WebProvider(marker="beta", gallery_url="https://example.com/beta/{id}"),
)


def test_no_example_is_held_to_the_clock_of_a_machine_other_runs_share():
    assert settings().deadline is None


# ---------------------------------------------------------------------------
# media_actions: make_web_url_from_path
# ---------------------------------------------------------------------------

@given(path=st.text(max_size=500))
def test_a_path_from_no_known_site_makes_no_web_url(path: str):
    assume("\\alpha\\" not in path.lower().replace("/", "\\"))
    assume("\\beta\\" not in path.lower().replace("/", "\\"))
    assert make_web_url_from_path(path, _PROVIDERS) == ""


@pytest.mark.parametrize("marker", ["alpha", "beta"])
@given(image_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=30))
def test_make_web_url_from_path_builds_each_providers_url(marker: str, image_id: str):
    path = f"C:\\images\\{marker}\\{image_id}.png"
    result = make_web_url_from_path(path, _PROVIDERS)
    assert result == f"https://example.com/{marker}/{image_id}"
