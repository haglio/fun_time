from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from app_support import glossary

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = ROOT / "glossary.json"
READ_BY_A_PERSON = (".py", ".md", ".vbs", ".ahk", ".toml")
FOLDERS_READ_BY_A_PERSON = ("docs", "fun_time", "fun_time_vr", "main_funestra", "satellite", "tests", "tools")


@pytest.fixture(scope="module")
def source():
    return glossary.source_lines(tuple(ROOT / folder for folder in ("fun_time", "fun_time_vr", "main_funestra", "satellite", "tools")))


def test_every_row_names_its_thing_and_stands_in_one_of_the_known_states():
    glossary.assert_rows_are_whole(GLOSSARY)


def test_every_word_the_glossary_says_the_code_says_is_still_in_the_code(source):
    glossary.assert_code_today_still_said(GLOSSARY, source)


def test_a_word_the_glossary_retired_is_gone_from_the_code(source):
    glossary.assert_retired_words_gone(GLOSSARY, source)


def test_every_screen_noun_in_the_code_has_a_row_in_the_glossary(source):
    glossary.assert_screen_nouns_have_rows(GLOSSARY, source)


def _spelled(spelling: str, kept: list[str], text: str) -> bool:
    for found in re.finditer(rf"\b{re.escape(spelling)}\b", text):
        if not any(text.startswith(name, found.start()) for name in kept):
            return True
    return False


def _files_a_person_reads():
    top = (path for path in ROOT.iterdir() if path.is_file())
    below = (path for folder in FOLDERS_READ_BY_A_PERSON for path in (ROOT / folder).rglob("*"))
    return [path for path in (*top, *below) if path.suffix in READ_BY_A_PERSON]


def test_a_spelling_the_glossary_took_away_is_in_no_file_a_person_reads():
    rows = [row for row in json.loads(GLOSSARY.read_text(encoding="utf-8"))["things"]
            if "spellings_gone" in row]
    left = sorted(
        f"{path.relative_to(ROOT).as_posix()}: {spelling}"
        for path in _files_a_person_reads()
        for row in rows
        for spelling in row["spellings_gone"]
        if _spelled(spelling, row["spellings_kept"], path.read_text(encoding="utf-8", errors="ignore")))
    assert not left, left
