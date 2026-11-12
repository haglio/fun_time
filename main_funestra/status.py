from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LibraryStatus:
    compilation: str = ""
    has_compilation: bool = False
    has_other_versions: bool = False
    jump_to: str = ""


def _range_a_restart_hands_back(snippet) -> tuple[int, int]:
    return snippet.bounds or (0, 0)


def status_fields(snippet, library: LibraryStatus | None = None) -> dict[str, str]:
    library = library or LibraryStatus()
    snippet_in_ms, snippet_out_ms = _range_a_restart_hands_back(snippet)
    return {
        "snippet_state": str(snippet.state),
        "snippet_in_ms": str(int(snippet_in_ms)),
        "snippet_out_ms": str(int(snippet_out_ms)),
        "compilation": library.compilation,
        "has_compilation": "1" if library.has_compilation else "0",
        "has_other_versions": "1" if library.has_other_versions else "0",
        "jump_to": library.jump_to,
    }
