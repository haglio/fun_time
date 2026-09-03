"""Property-based tests using hypothesis for fuzz-testing pure functions.

Phase 4D of the cleanup plan. These tests verify that pure parsing and
formatting functions never crash on arbitrary input.
"""
from __future__ import annotations

import pytest

from hypothesis import given, assume
from hypothesis import strategies as st

from fun_time.content import WebProvider
from fun_time.media_actions import csv_escape, to_file_uri, make_web_url_from_path

# Explicit providers keep these independent of the ambient content overlay
# (the real content.local.json is absent on a public checkout).
_PROVIDERS = (
    WebProvider(marker="alpha", gallery_url="https://example.com/alpha/{id}"),
    WebProvider(marker="beta", gallery_url="https://example.com/beta/{id}"),
)


# ---------------------------------------------------------------------------
# media_actions: csv_escape
# ---------------------------------------------------------------------------

@given(value=st.text(max_size=500))
def test_csv_escape_always_wraps_in_quotes(value: str):
    result = csv_escape(value)
    assert result.startswith('"')
    assert result.endswith('"')



@given(value=st.text(max_size=500))
def test_csv_escape_doubles_internal_quotes(value: str):
    result = csv_escape(value)
    # Strip outer quotes and verify no lone internal quotes
    inner = result[1:-1]
    # All quotes in the inner part should be doubled
    i = 0
    while i < len(inner):
        if inner[i] == '"':
            assert i + 1 < len(inner) and inner[i + 1] == '"', \
                f"Lone quote at position {i} in escaped value"
            i += 2
        else:
            i += 1


# ---------------------------------------------------------------------------
# media_actions: to_file_uri
# ---------------------------------------------------------------------------

@given(path=st.text(min_size=1, max_size=500))
def test_to_file_uri_starts_with_file_prefix(path: str):
    result = to_file_uri(path)
    assert result.startswith("file:///")


def test_to_file_uri_of_nothing_is_nothing():
    # The one input the prefix property excludes: no path means no URI at
    # all, not "file:///" pointing nowhere.
    assert to_file_uri("") == ""



# ---------------------------------------------------------------------------
# media_actions: make_web_url_from_path
# ---------------------------------------------------------------------------

@given(path=st.text(max_size=500))
def test_make_web_url_from_path_returns_empty_for_unknown_sites(path: str):
    assume("\\alpha\\" not in path.lower().replace("/", "\\"))
    assume("\\beta\\" not in path.lower().replace("/", "\\"))
    assert make_web_url_from_path(path, _PROVIDERS) == ""


@pytest.mark.parametrize("marker", ["alpha", "beta"])
@given(image_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=30))
def test_make_web_url_from_path_builds_each_providers_url(marker: str, image_id: str):
    path = f"C:\\images\\{marker}\\{image_id}.png"
    result = make_web_url_from_path(path, _PROVIDERS)
    assert result == f"https://example.com/{marker}/{image_id}"
