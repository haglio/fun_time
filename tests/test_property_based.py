"""Property-based tests using hypothesis for fuzz-testing pure functions.

Phase 4D of the cleanup plan. These tests verify that pure parsing and
formatting functions never crash on arbitrary input.
"""
from __future__ import annotations

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
def test_csv_escape_never_crashes(value: str):
    result = csv_escape(value)
    assert isinstance(result, str)
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

@given(path=st.text(max_size=500))
def test_to_file_uri_never_crashes(path: str):
    result = to_file_uri(path)
    assert isinstance(result, str)


@given(path=st.text(min_size=1, max_size=500))
def test_to_file_uri_starts_with_file_prefix(path: str):
    result = to_file_uri(path)
    assert result.startswith("file:///")



# ---------------------------------------------------------------------------
# media_actions: make_web_url_from_path
# ---------------------------------------------------------------------------

@given(path=st.text(max_size=500))
def test_make_web_url_from_path_never_crashes(path: str):
    result = make_web_url_from_path(path, _PROVIDERS)
    assert isinstance(result, str)


@given(path=st.text(max_size=500))
def test_make_web_url_from_path_returns_empty_for_unknown_sites(path: str):
    assume("\\alpha\\" not in path.lower().replace("/", "\\"))
    assume("\\beta\\" not in path.lower().replace("/", "\\"))
    assert make_web_url_from_path(path, _PROVIDERS) == ""


@given(image_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=30))
def test_make_web_url_from_path_builds_provider2_url(image_id: str):
    path = f"C:\\images\\beta\\{image_id}.png"
    result = make_web_url_from_path(path, _PROVIDERS)
    assert result == f"https://example.com/beta/{image_id}"


@given(image_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=30))
def test_make_web_url_from_path_builds_provider_url(image_id: str):
    path = f"C:\\images\\alpha\\{image_id}.png"
    result = make_web_url_from_path(path, _PROVIDERS)
    assert result == f"https://example.com/alpha/{image_id}"
