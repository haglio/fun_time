from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import fun_time_vr

THE_DESKTOPS_PACKAGES = ("fun_time", "main_funestra", "satellite", "shared_ui", "funestra", "app_support")

SMALLEST_BODY_WORTH_SHARING = 12

THE_HEADSETS_OWN_WORDS = {
    ("layout", "MAIN"): "the headset's name for a screen, kept in its layout file",
    ("layout", "PORTRAIT"): "the headset's name for a screen, kept in its layout file",
    ("layout", "LANDSCAPE"): "the headset's name for a screen, kept in its layout file",
    ("layout", "DASH"): "the headset's name for a screen, kept in its layout file",
    ("library_listing_cache", "_VIDEOS"): "a key of the headset's own listing file",
}


def _modules(package: Path) -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(package.rglob("*.py")):
        yield path.stem, ast.parse(path.read_text(encoding="utf-8"))


def _package(name: str) -> Path:
    return Path(importlib.import_module(name).__file__).parent


def _bodies(package: Path) -> Iterator[tuple[str, str]]:
    for module, tree in _modules(package):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.Module(body=node.body, type_ignores=[])
                if sum(1 for _ in ast.walk(body)) >= SMALLEST_BODY_WORTH_SHARING:
                    yield ast.dump(body), f"{module}.{node.name}"


def _words(package: Path) -> Iterator[tuple[str, tuple[str, str]]]:
    for module, tree in _modules(package):
        for node in tree.body:
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and len(value.value) > 3):
                continue
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    yield value.value, (module, target.id)


@cache
def _the_desktops_bodies() -> dict[str, str]:
    return {body: where for name in THE_DESKTOPS_PACKAGES for body, where in _bodies(_package(name))}


@cache
def _the_desktops_words() -> dict[str, tuple[str, str]]:
    return {word: where for name in THE_DESKTOPS_PACKAGES for word, where in _words(_package(name))}


def test_no_headset_function_does_what_a_desktop_one_already_does():
    desktops = _the_desktops_bodies()

    copies = [f"{where} copies {desktops[body]}"
              for body, where in _bodies(Path(fun_time_vr.__file__).parent) if body in desktops]

    assert copies == []


def test_the_headset_spells_no_name_the_desktop_already_spells():
    desktops = _the_desktops_words()

    spelled_again = [f"{module}.{name} = {word!r} spells {desktops[word]} again"
                     for word, (module, name) in _words(Path(fun_time_vr.__file__).parent)
                     if word in desktops and (module, name) not in THE_HEADSETS_OWN_WORDS]

    assert spelled_again == []


def test_every_word_let_through_is_still_spelled_on_both_sides():
    desktops = _the_desktops_words()
    headsets = {where: word for word, where in _words(Path(fun_time_vr.__file__).parent)}

    assert [where for where in THE_HEADSETS_OWN_WORDS
            if headsets.get(where) not in desktops] == []


_A_BODY = ("    for threshold in sorted(TABLE, reverse=True):\n"
           "        if level >= threshold:\n"
           "            return TABLE[threshold]\n"
           "    return DEFAULT\n")


def _a_package(folder: Path, source: str) -> Path:
    folder.mkdir()
    (folder / "made_up.py").write_text(source, encoding="utf-8")
    return folder


def test_a_copy_is_caught_whatever_its_function_is_called(tmp_path):
    desktop = _a_package(tmp_path / "desktop", f"def theirs(level):\n{_A_BODY}")
    headset = _a_package(tmp_path / "headset", f"def mine(level):\n{_A_BODY}")

    caught = {body for body, _ in _bodies(headset)}

    assert caught
    assert caught == {body for body, _ in _bodies(desktop)}


def test_a_name_spelled_again_is_caught_whatever_it_is_called(tmp_path):
    desktop = _a_package(tmp_path / "desktop", 'THEIRS = "made_up_verb"\n')
    headset = _a_package(tmp_path / "headset", 'MINE: str = "made_up_verb"\n')

    assert [word for word, _ in _words(headset)] == ["made_up_verb"]
    assert [word for word, _ in _words(desktop)] == ["made_up_verb"]
