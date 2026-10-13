"""The shells call the shared code with words it takes, checked without building them.

A shell is a module no test can build -- it needs the libmpv DLL, a GL context,
a headset, a real Chrome or the machine's own sound devices -- so the suites
reach every line around it and none of its own.  What hides there is a call into
the code the apps share: the shared code renames one of the words it takes, both
suites stay green, and he meets it as a room that shows its loading screen and
then goes.  His 2026-10-12 session died that way, on a word that had become
``host_controls`` in the funestra this computer had installed:

    TypeError: Funestra.__init__() got an unexpected keyword argument 'host_block'

So each shell's source is read rather than run, and every word it hands a name
borrowed from the shared packages is held against what that name takes now.
"""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The shells fun_time's CLAUDE.md names as untested on purpose, and the only
#: ones whose calls nothing else can catch.
SHELLS = (
    "fun_time_vr/funestras.py",
    "fun_time_vr/render.py",
    "fun_time_vr/vr_session.py",
    "fun_time_vr/video_thread.py",
    "fun_time_vr/gl_contexts.py",
    "fun_time_vr/windows_sound.py",
    "satellite/app.py",
    "fun_time/chrome_accessibility.py",
)
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


def test_every_word_a_shell_hands_the_shared_code_is_one_it_takes():
    checked = 0
    for shell in SHELLS:
        tree = ast.parse((ROOT / shell).read_text(encoding="utf-8"))
        borrowed = _names_borrowed_from_the_shared_code(tree)
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not call.keywords:
                continue
            if any(word.arg is None for word in call.keywords):
                continue  # a call that spreads a mapping names no words here
            target = _what_it_calls(borrowed, ast.unparse(call.func))
            if target is None:
                continue
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):
                continue  # something the interpreter will not describe
            signature.bind_partial(
                **dict.fromkeys(word.arg for word in call.keywords))
            checked += 1
    assert checked, "no shell calls the shared code by a borrowed name any more"
