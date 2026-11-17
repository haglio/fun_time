from __future__ import annotations

import ast
from pathlib import Path

from fun_time.voice_commands import VOICE_COMMANDS

_REPO = Path(__file__).resolve().parents[1]
_PRODUCT_PACKAGES = ("fun_time", "fun_time_vr", "satellite", "main_funestra")


def _product_sources() -> list[Path]:
    return sorted(source for package in _PRODUCT_PACKAGES for source in (_REPO / package).rglob("*.py"))


def _string_literals(source: Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _is_a_sentence(text: str) -> bool:
    return " " in text.strip()


def test_no_sentence_fun_time_shows_or_logs_says_upscale():
    said = sorted(
        f"{source.relative_to(_REPO).as_posix()}: {literal}"
        for source in _product_sources()
        for literal in _string_literals(source)
        if "upscal" in literal.lower() and _is_a_sentence(literal))

    assert said == []


def test_no_spoken_phrase_says_upscale():
    assert sorted(phrase for phrase in VOICE_COMMANDS if "upscal" in phrase) == []
