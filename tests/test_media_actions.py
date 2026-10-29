from __future__ import annotations

from pathlib import Path

from fun_time.content import WebProvider
from fun_time.media_actions import make_web_url_from_path, move_to_weird

# Explicit providers keep these tests independent of the ambient content overlay
# (the real content.local.json is absent on a public checkout).
_PROVIDERS = (
    WebProvider(marker="alpha", gallery_url="https://example.com/alpha/{id}"),
    WebProvider(marker="beta", gallery_url="https://example.com/beta/{id}"),
)


def test_make_web_url_from_path_supports_known_roots():
    assert make_web_url_from_path(r"C:\images\alpha\abc_123.png", _PROVIDERS) == "https://example.com/alpha/abc"
    assert make_web_url_from_path(r"C:\images\beta\def_456.jpg", _PROVIDERS) == "https://example.com/beta/def"
    assert make_web_url_from_path(r"C:\images\other\ghi_789.jpg", _PROVIDERS) == ""


def test_move_to_weird_leaves_nothing_for_a_missing_source(tmp_path: Path):
    """A clip already gone (trashed twice, or moved by hand) is not an error:
    the move reports where it WOULD have landed and writes nothing."""
    weird_dir = tmp_path / "weird"

    dest = move_to_weird(weird_dir, tmp_path / "vanished.mp4")

    assert dest == weird_dir / "vanished.mp4"
    assert not dest.exists()


def test_move_to_weird_moves_file_and_avoids_name_collisions(tmp_path: Path):
    weird_dir = tmp_path / "weird"
    src_a = tmp_path / "clip.mp4"
    src_b = tmp_path / "clip2.mp4"
    src_a.write_text("a", encoding="utf-8")
    src_b.write_text("b", encoding="utf-8")
    (weird_dir / "clip.mp4").parent.mkdir(parents=True, exist_ok=True)
    (weird_dir / "clip.mp4").write_text("existing", encoding="utf-8")

    dest = move_to_weird(weird_dir, src_a)
    dest_dup = move_to_weird(weird_dir, src_b, destination_name="clip.mp4")

    assert dest == weird_dir / "clip__dup1.mp4"
    assert dest_dup == weird_dir / "clip__dup2.mp4"
    assert dest.read_text(encoding="utf-8") == "a"
    assert dest_dup.read_text(encoding="utf-8") == "b"
