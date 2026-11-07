from __future__ import annotations

from collections.abc import Mapping

from funestra.video_families import normalized_text

from .command_value import set_command

Acts = Mapping[str, tuple[str, ...]]

_SCOPE_TOKENS: tuple[str, ...] = ("satellites", "portrait", "landscape")


def named_first_or_last(funestra_word: str, words: str, command: str) -> dict[str, str]:
    return dict.fromkeys((f"{funestra_word} {words}", f"{words} {funestra_word}"), command)


def filter_voice_commands(acts: Acts) -> dict[str, str]:
    out: dict[str, str] = {}
    for query, forms in acts.items():
        for heard in map(normalized_text, forms):
            out[heard] = set_command("satellites", query)
            for satellite in _SCOPE_TOKENS:
                out.update(named_first_or_last(satellite, heard, set_command(satellite, query)))
    return out


def set_commands_for_scope(scope: str, acts: Acts) -> tuple[str, ...]:
    return tuple(set_command(scope, query) for query in acts)


def display_forms(acts: Acts) -> tuple[str, ...]:
    return tuple(acts)
