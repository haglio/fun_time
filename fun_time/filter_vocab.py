"""Spoken filter vocabulary and command decoding for the satellite players.

A metadata filter is issued by voice: an optional orientation scope
("portrait"/"landscape", or none for both players) plus an act drawn from the
library's ``video.action`` values.  This module is the single source of truth
mapping those to dispatch commands.  It stays free of the speech runtime so the
command reference and tests can import it cheaply — the same reason
:mod:`fun_time.voice_commands` is split from :mod:`fun_time.voice_control`.

The act vocabulary is content, not logic, so it lives in a JSON overlay
(``content.local.json``, git-ignored) with a committed ``content.example.json``
placeholder — the recognizer behaves the same whichever is loaded.  Some spoken
forms deliberately differ from the query they match: a word the small speech
model does not know is voiced with in-vocabulary words while the command keeps
the real query (the same trick the mode commands use, "genau" for "go now").
Queries stay lowercase; a single query substring-matches every metadata value
that contains it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .content import EXAMPLE_CONTENT, LOCAL_CONTENT, load_content

Acts = Mapping[str, tuple[str, ...]]


def load_filter_acts(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> dict[str, tuple[str, ...]]:
    """Canonical query -> spoken forms, from the local overlay or the example.

    The git-ignored ``content.local.json`` holds the real vocabulary; the
    committed ``content.example.json`` is a tame placeholder used whenever it is
    absent (a fresh or public checkout).
    """
    data = load_content(local_path, example_path)
    return {query: tuple(forms) for query, forms in data["filter_acts"].items()}


# The canonical query (matched against a video's metadata) -> spoken forms to
# listen for.  Loaded once at import; see :func:`load_filter_acts`.
FILTER_ACTS: dict[str, tuple[str, ...]] = load_filter_acts()

# Spoken scope word -> command scope token.  "" means no orientation was said,
# so the filter applies to both players.
_SCOPES: dict[str, str] = {"": "both", "portrait": "portrait", "landscape": "landscape"}

_SCOPE_TOKENS: tuple[str, ...] = ("both", "portrait", "landscape")

# Spoken clear phrase -> the scope it clears.
_CLEAR_PHRASES: dict[str, str] = {
    "clear filter": "both",
    "show everything": "both",
    "clear portrait": "portrait",
    "clear landscape": "landscape",
}


def _slug(query: str) -> str:
    return query.replace(" ", "_")


def _unslug(slug: str) -> str:
    return slug.replace("_", " ")


def set_command(scope: str, query: str) -> str:
    return f"filter_{scope}_{_slug(query)}"


def clear_command(scope: str) -> str:
    return f"filter_{scope}_clear"


def decode_filter_command(command: str) -> tuple[str, str] | None:
    """``(scope, query)`` for a filter dispatch command, or None if it isn't one.

    ``query`` is ``""`` for a clear command; ``scope`` is one of
    both/portrait/landscape.  Decoding is purely structural, so an unknown act
    still round-trips (and simply matches nothing downstream).
    """
    if not command.startswith("filter_"):
        return None
    rest = command[len("filter_"):]
    for scope in _SCOPE_TOKENS:
        prefix = f"{scope}_"
        if not rest.startswith(prefix):
            continue
        remainder = rest[len(prefix):]
        if remainder == "clear":
            return scope, ""
        return scope, _unslug(remainder)
    return None


def filter_voice_commands(acts: Acts = FILTER_ACTS) -> dict[str, str]:
    """Spoken phrase -> dispatch command for every filter trigger."""
    out: dict[str, str] = {}
    for query, forms in acts.items():
        for scope_word, scope in _SCOPES.items():
            command = set_command(scope, query)
            for form in forms:
                out[f"{scope_word} {form}".strip()] = command
    for phrase, scope in _CLEAR_PHRASES.items():
        out[phrase] = clear_command(scope)
    return out


def set_commands_for_scope(scope: str, acts: Acts = FILTER_ACTS) -> tuple[str, ...]:
    """Every set (non-clear) command for *scope* — for the command reference."""
    return tuple(set_command(scope, query) for query in acts)


def display_forms(acts: Acts = FILTER_ACTS) -> tuple[str, ...]:
    """The acts under their real names — what the reference shows.

    A spoken form is what the *recognizer* can hear, and that is not always what
    the act is called: where the small model has no token for a word, the form
    spells it with tokens the model does have, which is what the model produces
    when the real word is said aloud.  The query keeps the real term, so that is
    what the reference shows — printing the workaround would teach the reader a
    word nobody uses for the thing, over a model limitation that is none of
    their business and that they never have to pronounce around.

    The spoken forms have no such reader: the grammar is built from
    :func:`filter_voice_commands`, so nothing outside this module needs them.
    """
    return tuple(acts)
