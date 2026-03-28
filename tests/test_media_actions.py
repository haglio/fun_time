from __future__ import annotations

from pathlib import Path

from fun_time.media_actions import (
    ensure_favs_csv_exists,
    ensure_in_favs,
    make_local_cell,
    make_web_cell,
    make_web_url_from_path,
    move_to_weird,
    remove_from_favs,
)


def test_make_web_url_from_path_supports_known_roots():
    assert make_web_url_from_path(r"C:\images\provider2\abc_123.png") == "https://example.net/image/abc"
    assert make_web_url_from_path(r"C:\images\provider\def_456.jpg") == "https://example.com/image/def"
    assert make_web_url_from_path(r"C:\images\other\ghi_789.jpg") == ""


def test_make_cells_build_clickable_csv_formulas():
    path = r"C:\folder with space\image_123.png"

    assert make_local_cell(path) == '=HYPERLINK("file:///C:/folder%20with%20space/image_123.png";"C:\\folder with space\\image_123.png")'
    assert make_web_cell(r"C:\root\provider2\hello_456.png") == '=HYPERLINK("https://example.net/image/hello";"https://example.net/image/hello")'


def test_ensure_in_favs_creates_header_and_appends_only_once(tmp_path: Path):
    favs = tmp_path / "favs.csv"
    full_path = r"C:\root\provider2\hello_456.png"

    ensure_in_favs(favs, full_path)
    ensure_in_favs(favs, full_path)

    lines = favs.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "local_file,web_url"
    assert len(lines) == 2
    assert "https://example.net/image/hello" in lines[1]


def test_remove_from_favs_preserves_other_rows_and_header(tmp_path: Path):
    favs = tmp_path / "favs.csv"
    keep = r"C:\root\provider2\keep_123.png"
    remove = r"C:\root\provider2\remove_456.png"
    ensure_favs_csv_exists(favs)
    ensure_in_favs(favs, keep)
    ensure_in_favs(favs, remove)

    remove_from_favs(favs, remove)

    text = favs.read_text(encoding="utf-8")
    assert "local_file,web_url" in text
    assert "keep_123" in text
    assert "remove_456" not in text


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
