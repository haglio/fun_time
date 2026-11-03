from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path

from .aiming import named_first_or_last
from .content import EXAMPLE_CONTENT, LOCAL_CONTENT, load_content

Acts = Mapping[str, tuple[str, ...]]


@functools.cache
def load_acts(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> dict[str, tuple[str, ...]]:
    data = load_content(local_path, example_path)
    return {query: tuple(forms) for query, forms in data["acts"].items()}


@functools.cache
def load_camera_words(
    local_path: Path = LOCAL_CONTENT,
    example_path: Path = EXAMPLE_CONTENT,
) -> tuple[str, ...]:
    return tuple(load_content(local_path, example_path)["cameras"])


_SCOPE_TOKENS: tuple[str, ...] = ("both", "portrait", "landscape")


def _slug(query: str) -> str:
    return query.replace(" ", "_")


def _unslug(slug: str) -> str:
    return slug.replace("_", " ")


def set_command(scope: str, query: str) -> str:
    return f"filter_{scope}_{_slug(query)}"


def decode_filter_command(command: str) -> tuple[str, str] | None:
    if not command.startswith("filter_"):
        return None
    rest = command[len("filter_"):]
    for scope in _SCOPE_TOKENS:
        prefix = f"{scope}_"
        if not rest.startswith(prefix):
            continue
        return scope, _unslug(rest[len(prefix):])
    return None


def filter_voice_commands(acts: Acts | None = None) -> dict[str, str]:
    acts = load_acts() if acts is None else acts
    out: dict[str, str] = {}
    for query, forms in acts.items():
        for form in forms:
            out[form] = set_command("both", query)
            for satellite in _SCOPE_TOKENS:
                out.update(named_first_or_last(satellite, form, set_command(satellite, query)))
    return out


def set_commands_for_scope(scope: str, acts: Acts | None = None) -> tuple[str, ...]:
    return tuple(set_command(scope, query)
                 for query in (load_acts() if acts is None else acts))


def display_forms(acts: Acts | None = None) -> tuple[str, ...]:
    return tuple(load_acts() if acts is None else acts)
