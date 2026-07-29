from __future__ import annotations

from pathlib import Path

from fun_time.rfb_tab_page import TabTarget, render_tab_page, tabs_dir, write_tab_pages
from fun_time.loopback_server import omnipause_url

REGEN_URL = "https://example.com/create#ft=%7B%22kind%22%3A%22image%22%7D"


def _video(tmp_path: Path, name: str = "clip.mp4") -> Path:
    video = tmp_path / name
    video.write_bytes(b"")
    return video


def test_render_tab_page_navigates_to_the_target_on_reload():
    html = render_tab_page(TabTarget(REGEN_URL, "https://example.com/image/abc"))

    assert f'"{REGEN_URL}"' in html
    assert "location.replace(target)" in html
    assert "'reload'" in html


def test_render_tab_page_shows_the_label_not_the_payload():
    """A regenerate URL is kilobytes of encoded prompt — the page names the fav."""
    html = render_tab_page(TabTarget(REGEN_URL, "https://example.com/image/abc"))

    assert '"https://example.com/image/abc"' in html
    assert "__FT_TARGET__" not in html
    assert "__FT_LABEL__" not in html
    assert "__FT_VIDEO__" not in html


def test_render_tab_page_escapes_a_script_breakout():
    html = render_tab_page(TabTarget("https://example.com/create#</script><script>evil()</script>", "l"))

    assert "<script>evil()" not in html


# --- the video being recreated ---


def test_render_tab_page_embeds_the_video_as_a_file_uri(tmp_path: Path):
    video = _video(tmp_path, "a clip.mp4")

    html = render_tab_page(TabTarget(REGEN_URL, "l", video_path=str(video)))

    assert f'"{video.as_uri()}"' in html


def test_render_tab_page_without_a_video_embeds_an_empty_source():
    html = render_tab_page(TabTarget(REGEN_URL, "l"))

    assert '"__FT_VIDEO__"' not in html
    assert 'var video = "";' in html


def test_render_tab_page_embeds_no_video_for_a_relative_path():
    """A path that is not absolute has no file URI; the page just omits the clip."""
    html = render_tab_page(TabTarget(REGEN_URL, "l", video_path="clip.mp4"))

    assert 'var video = "";' in html


def test_render_tab_page_decodes_only_while_the_tab_is_visible(tmp_path: Path):
    """Ten background tabs all decoding video would burn the CPU for nothing."""
    html = render_tab_page(TabTarget(REGEN_URL, "l", video_path=str(_video(tmp_path))))

    assert "visibilitychange" in html
    assert "clip.pause()" in html


def test_render_tab_page_asks_this_session_whether_it_is_omnipaused(tmp_path: Path):
    """A page pointed at the wrong URL just never freezes — silently, forever."""
    html = render_tab_page(TabTarget(REGEN_URL, "l", video_path=str(_video(tmp_path))))

    assert f'"{omnipause_url()}"' in html


def test_render_tab_page_centers_the_clip(tmp_path: Path):
    """The clip is revealed as a block box narrower than the text around it, so
    the container's text-align cannot center it — only auto side margins can."""
    html = render_tab_page(TabTarget(REGEN_URL, "l", video_path=str(_video(tmp_path))))

    assert "margin: 0 auto" in html


# --- writing pages ---


def test_tabs_dir_hangs_off_the_state_dir(tmp_path: Path):
    assert tabs_dir(tmp_path) == tmp_path / "rfb_tabs"


def test_write_tab_pages_returns_one_file_uri_per_target(tmp_path: Path):
    pages = tmp_path / "rfb_tabs"

    uris = write_tab_pages(pages, [TabTarget(REGEN_URL, "a"), TabTarget("https://b", "b")])

    assert len(uris) == 2
    assert all(uri.startswith("file:///") for uri in uris)
    assert REGEN_URL in (pages / "tab_01.html").read_text(encoding="utf-8")
    assert "https://b" in (pages / "tab_02.html").read_text(encoding="utf-8")


def test_write_tab_pages_clears_pages_from_the_previous_session(tmp_path: Path):
    pages = tmp_path / "rfb_tabs"
    pages.mkdir(parents=True)
    stale = pages / "tab_09.html"
    stale.write_text("last session", encoding="utf-8")

    write_tab_pages(pages, [TabTarget("https://a", "a")])

    assert not stale.exists()
    assert (pages / "tab_01.html").is_file()
