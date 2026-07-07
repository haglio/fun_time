"""Spoken filter vocabulary and command decoding for the satellite VLCs.

A metadata filter is issued by voice: an optional orientation scope
("portrait"/"landscape", or none for both VLCs) plus an act drawn from the
library's real ``video.action`` values.  This module is the single source of
truth mapping those to dispatch commands.  It is kept free of the vosk runtime
so the command reference and tests can import it cheaply — the same reason
:mod:`fun_time.voice_commands` is split from :mod:`fun_time.voice_control`.
"""
from __future__ import annotations

# Canonical query (the substring matched against a video's metadata) -> the
# spoken forms the recognizer should listen for.  The vosk small model's lexicon
# lacks the generated-specific compounds ("alpha", "gamma", ...), so each act is
# voiced with plain-English words — the same trick the mode commands use
# ("genau" listens for "go now").  Queries stay lowercase; a single query such
# as "insertion" substring-matches both "Oral Insertion" and "redacted Insertion".
FILTER_ACTS: dict[str, tuple[str, ...]] = {
    "alpha": ("alpha form",),
    "epsilon": ("epsilon",),
    "gamma": ("gamma",),
    "delta": ("delta",),
    "beta gamma": ("beta gamma",),
    "insertion": ("insertion",),
    "zeta massage": ("redacted massage",),
    "dancing": ("dancing",),
    "kissing": ("kissing",),
    "eta form": ("come on face",),
}

# Spoken scope word -> command scope token.  "" means no orientation was said,
# so the filter applies to both VLCs.
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


def filter_voice_commands() -> dict[str, str]:
    """Spoken phrase -> dispatch command for every filter trigger."""
    out: dict[str, str] = {}
    for query, forms in FILTER_ACTS.items():
        for scope_word, scope in _SCOPES.items():
            command = set_command(scope, query)
            for form in forms:
                out[f"{scope_word} {form}".strip()] = command
    for phrase, scope in _CLEAR_PHRASES.items():
        out[phrase] = clear_command(scope)
    return out


def set_commands_for_scope(scope: str) -> tuple[str, ...]:
    """Every set (non-clear) command for *scope* — for the command reference."""
    return tuple(set_command(scope, query) for query in FILTER_ACTS)


def spoken_forms_for_both() -> tuple[str, ...]:
    """The bare (unscoped) spoken forms — one recognizer phrase per act form."""
    return tuple(form for forms in FILTER_ACTS.values() for form in forms)
