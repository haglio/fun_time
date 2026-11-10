from __future__ import annotations

import ast
import importlib
import inspect
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _modules_no_unit_test_builds() -> tuple[str, ...]:
    with (ROOT / "pyproject.toml").open("rb") as config:
        excused = tomllib.load(config)["tool"]["haglio"]["coverage"]["not-unit-tested"]
    return tuple(module["path"] for module in excused)


EXCUSED_WHEN_THIS_CHECK_WAS_WRITTEN = ("satellite/app.py", "fun_time_vr/video_thread.py")
SHELLS = (*_modules_no_unit_test_builds(), *EXCUSED_WHEN_THIS_CHECK_WAS_WRITTEN)
SHARED_PACKAGES = ("funestra", "shared_ui", "app_support")


def _names_borrowed_from_the_shared_code(tree: ast.Module) -> dict[str, tuple[str, str]]:
    borrowed = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module.split(".")[0] not in SHARED_PACKAGES:
            continue
        for alias in node.names:
            borrowed[alias.asname or alias.name] = (node.module, alias.name)
    return borrowed


def _what_it_calls(borrowed: dict[str, tuple[str, str]], spelled: str):
    head, *rest = spelled.split(".")
    if head not in borrowed:
        return None
    module, attribute = borrowed[head]
    found = getattr(importlib.import_module(module), attribute, None)
    for piece in rest:
        found = getattr(found, piece, None)
    return found if callable(found) else None


def _spreads_a_mapping_whose_words_the_source_does_not_spell(call: ast.Call) -> bool:
    return any(word.arg is None for word in call.keywords)


def _signature_if_python_can_report_one(target) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError):
        return None


def _calls_handing_the_shared_code_words_it_takes(source: str) -> int:
    tree = ast.parse(source)
    borrowed = _names_borrowed_from_the_shared_code(tree)
    checked = 0
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not call.keywords:
            continue
        if _spreads_a_mapping_whose_words_the_source_does_not_spell(call):
            continue
        target = _what_it_calls(borrowed, ast.unparse(call.func))
        if target is None:
            continue
        signature = _signature_if_python_can_report_one(target)
        if signature is None:
            continue
        signature.bind_partial(
            **dict.fromkeys(word.arg for word in call.keywords))
        checked += 1
    return checked


def test_every_word_a_shell_hands_the_shared_code_is_one_it_takes():
    checked = sum(
        _calls_handing_the_shared_code_words_it_takes((ROOT / shell).read_text(encoding="utf-8"))
        for shell in SHELLS)

    assert checked, "no shell calls the shared code by a borrowed name any more"


def test_a_word_the_shared_code_renamed_is_caught_in_a_shell_nothing_builds():
    a_shell_still_saying_the_old_word = (
        "from app_support.siblings import primary_checkout_of\n"
        "primary_checkout_of(the_word_before_the_rename=1)\n")

    with pytest.raises(TypeError, match="the_word_before_the_rename"):
        _calls_handing_the_shared_code_words_it_takes(a_shell_still_saying_the_old_word)
