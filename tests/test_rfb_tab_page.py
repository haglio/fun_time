from __future__ import annotations

from pathlib import Path

from fun_time.random_favs_browser import FavTarget
from fun_time.rfb_tab_page import render_tab_page, write_tab_pages

REGEN_URL = "https://example.com/create#ft=%7B%22kind%22%3A%22image%22%7D"


def test_render_tab_page_navigates_to_the_target_on_reload():
    html = render_tab_page(REGEN_URL, "https://example.com/image/abc")

    assert f'"{REGEN_URL}"' in html
    assert "location.replace(target)" in html
    assert "'reload'" in html


def test_render_tab_page_shows_the_label_not_the_payload():
    """A regenerate URL is kilobytes of encoded prompt — the page names the fav."""
    html = render_tab_page(REGEN_URL, "https://example.com/image/abc")

    assert '"https://example.com/image/abc"' in html
    assert "__FT_TARGET__" not in html
    assert "__FT_LABEL__" not in html


def test_render_tab_page_escapes_a_script_breakout():
    html = render_tab_page("https://example.com/create#</script><script>evil()</script>", "l")

    assert "<script>evil()" not in html


def test_write_tab_pages_returns_one_file_uri_per_target(tmp_path: Path):
    tabs_dir = tmp_path / "rfb_tabs"

    uris = write_tab_pages(tabs_dir, [FavTarget(REGEN_URL, "a"), FavTarget("https://b", "b")])

    assert len(uris) == 2
    assert all(uri.startswith("file:///") for uri in uris)
    assert REGEN_URL in (tabs_dir / "tab_01.html").read_text(encoding="utf-8")
    assert "https://b" in (tabs_dir / "tab_02.html").read_text(encoding="utf-8")


def test_write_tab_pages_clears_pages_from_the_previous_session(tmp_path: Path):
    tabs_dir = tmp_path / "rfb_tabs"
    tabs_dir.mkdir(parents=True)
    stale = tabs_dir / "tab_09.html"
    stale.write_text("last session", encoding="utf-8")

    write_tab_pages(tabs_dir, [FavTarget("https://a", "a")])

    assert not stale.exists()
    assert (tabs_dir / "tab_01.html").is_file()
