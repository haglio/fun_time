"""Property-based tests using hypothesis for fuzz-testing pure functions.

Phase 4D of the cleanup plan. These tests verify that pure parsing and
formatting functions never crash on arbitrary input.
"""
from __future__ import annotations

from hypothesis import given, assume
from hypothesis import strategies as st

from fun_time.vlc_actions import decode_file_uri
from fun_time.media_actions import csv_escape, to_file_uri, make_web_url_from_path


# ---------------------------------------------------------------------------
# vlc_actions: decode_file_uri
# ---------------------------------------------------------------------------

@given(uri=st.text(max_size=500))
def test_decode_file_uri_never_crashes(uri: str):
    result = decode_file_uri(uri)
    assert isinstance(result, str)



@given(path=st.text(alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")), min_size=1, max_size=200))
def test_decode_file_uri_roundtrips_simple_paths(path: str):
    """For paths without special URL characters, encode then decode should roundtrip."""
    assume("/" not in path)
    assume("\\" not in path)
    assume("%" not in path)
    assume("#" not in path)
    assume("?" not in path)
    uri = "file:///" + path
    result = decode_file_uri(uri)
    assert result == path


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
    result = make_web_url_from_path(path)
    assert isinstance(result, str)


@given(path=st.text(max_size=500))
def test_make_web_url_from_path_returns_empty_for_unknown_sites(path: str):
    assume("\\provider2\\" not in path.lower().replace("/", "\\"))
    assume("\\provider\\" not in path.lower().replace("/", "\\"))
    assert make_web_url_from_path(path) == ""


@given(image_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=30))
def test_make_web_url_from_path_builds_provider2_url(image_id: str):
    path = f"C:\\images\\provider2\\{image_id}.png"
    result = make_web_url_from_path(path)
    assert result == f"https://example.net/image/{image_id}"


@given(image_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=30))
def test_make_web_url_from_path_builds_provider_url(image_id: str):
    path = f"C:\\images\\provider\\{image_id}.png"
    result = make_web_url_from_path(path)
    assert result == f"https://example.com/image/{image_id}"


# ---------------------------------------------------------------------------
# vlc_actions: XML parsing regexes (extracted from HTTP functions)
# ---------------------------------------------------------------------------

import re

_RE_STATE = re.compile(r"<state>([^<]+)</state>")
_RE_LOOP = re.compile(r"<loop>([^<]+)</loop>")
_RE_REPEAT = re.compile(r"<repeat>([^<]+)</repeat>")
_RE_CURRENT = re.compile(r'uri="([^"]+)"[^>]*current="current"', re.IGNORECASE)


@given(xml=st.text(max_size=1000))
def test_vlc_state_regex_never_crashes(xml: str):
    result = _RE_STATE.search(xml)
    if result:
        assert isinstance(result.group(1), str)


@given(xml=st.text(max_size=1000))
def test_vlc_loop_repeat_regex_never_crashes(xml: str):
    loop = _RE_LOOP.search(xml)
    repeat = _RE_REPEAT.search(xml)
    if loop:
        assert isinstance(loop.group(1), str)
    if repeat:
        assert isinstance(repeat.group(1), str)


@given(xml=st.text(max_size=1000))
def test_vlc_current_uri_regex_never_crashes(xml: str):
    result = _RE_CURRENT.search(xml)
    if result:
        assert isinstance(result.group(1), str)
