from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path

from .command_value import set_command
from .content import EXAMPLE_CONTENT, LOCAL_CONTENT, load_content

Acts = Mapping[str, tuple[str, ...]]


@functools.cache
def load_acts(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> dict[str, tuple[str, ...]]:
    data = load_content(local_path, example_path)
    return {query: tuple(forms) for query, forms in data["acts"].items()}


_SCOPE_TOKENS: tuple[str, ...] = ("satellites", "portrait", "landscape")


def named_first_or_last(funestra_word: str, words: str, command: str) -> dict[str, str]:
    return dict.fromkeys((f"{funestra_word} {words}", f"{words} {funestra_word}"), command)


def filter_voice_commands(acts: Acts | None = None) -> dict[str, str]:
    acts = load_acts() if acts is None else acts
    out: dict[str, str] = {}
    for query, forms in acts.items():
        for form in forms:
            out[form] = set_command("satellites", query)
            for satellite in _SCOPE_TOKENS:
                out.update(named_first_or_last(satellite, form, set_command(satellite, query)))
    return out


def set_commands_for_scope(scope: str, acts: Acts | None = None) -> tuple[str, ...]:
    return tuple(set_command(scope, query)
                 for query in (load_acts() if acts is None else acts))


def display_forms(acts: Acts | None = None) -> tuple[str, ...]:
    return tuple(load_acts() if acts is None else acts)
