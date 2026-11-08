from __future__ import annotations

from pathlib import Path

from fun_time.loading_reveal import LoadingReveal
from fun_time.overlay_progress import PROGRESS_FILENAME


def _reveal_during_startup(tmp_path: Path) -> LoadingReveal:
    (tmp_path / PROGRESS_FILENAME).write_text("70/340|Launching companions...", encoding="utf-8")
    return LoadingReveal(tmp_path)


def test_a_minimize_edge_during_startup_is_startups_not_a_gesture(tmp_path: Path):
    reveal = _reveal_during_startup(tmp_path)

    assert reveal.deferred is True
    assert reveal.routing_suppressed is True


def test_only_the_first_restore_edge_after_startup_belongs_to_startup(tmp_path: Path):
    reveal = _reveal_during_startup(tmp_path)

    assert reveal.took_the_reveals_own_restore() is True
    assert reveal.took_the_reveals_own_restore() is False
    assert reveal.routing_suppressed is False


def test_a_session_that_never_waited_has_no_restore_edge_of_startups(tmp_path: Path):
    reveal = LoadingReveal(tmp_path)

    assert reveal.deferred is False
    assert reveal.routing_suppressed is False
    assert reveal.took_the_reveals_own_restore() is False
