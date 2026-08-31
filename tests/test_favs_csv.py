"""The favs.csv HYPERLINK format, described once (finding A_dispatch/design/013).

favs.csv is public surface — evolver reads it, and it opens in a spreadsheet —
so the FORMAT must not change; this consolidates the writer's and the reader's
descriptions of the same bytes and holds them together with a round trip.
"""
from __future__ import annotations

from fun_time.favs_csv import FAVS_HEADER, hyperlink_cell, hyperlink_parts


def test_a_written_cell_reads_back_as_its_two_arguments():
    cell = hyperlink_cell("file:///C:/fabricated/clip.mp4", r"C:\fabricated\clip.mp4")

    assert hyperlink_parts(cell) == (
        "file:///C:/fabricated/clip.mp4", r"C:\fabricated\clip.mp4")


def test_the_cell_is_the_exact_excel_formula_evolver_reads():
    assert hyperlink_cell("https://example.com/a", "https://example.com/a") == (
        '=HYPERLINK("https://example.com/a";"https://example.com/a")')


def test_a_non_formula_cell_answers_none():
    assert hyperlink_parts("https://example.com/plain") is None
    assert hyperlink_parts("") is None


def test_the_header_is_the_two_column_names():
    assert FAVS_HEADER == "local_file,web_url"
