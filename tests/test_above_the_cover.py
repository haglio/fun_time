"""Unit tests: how long a window sat over the loading cover, from samples.

The integration test that uses this asks the one question the user can see —
did anything show through the scrim long enough to be drawn — so what it
measures has to mean what it says.  It did not: a stay was inferred from the
gaps between samples of one window, any gap under 20ms counting as the same
stay, while the cover takes the top back in 16ms.  Two separate appearances
either side of the cover's own answer were therefore reported as one stay
twice as long, and a run that broke nothing failed.
"""
from __future__ import annotations

from tests.integration.above_the_cover import VISIBLE_MS, AboveTheCover, stays_from

PANEL, OTHER, NOTHING = 4242, 1717, 0
POLL_MS = 2.0


def _walk(*spans: tuple[float, int]) -> list[tuple[float, int, str]]:
    """Samples every 2ms across *spans*, each (how many ms, what was above)."""
    samples: list[tuple[float, int, str]] = []
    now = 0.0
    for ms, hwnd in spans:
        end = now + ms
        while now < end:
            samples.append((now / 1000, hwnd, {PANEL: "panel", OTHER: "other"}.get(hwnd, "")))
            now += POLL_MS
    return samples


def test_the_covers_own_answer_ends_a_stay():
    """The cover back on top is a sample naming nothing, and it ends whatever
    was above it — so an appearance is never joined to the next one across it,
    however close the two are.  Sixteen milliseconds apart is the usual case,
    not an edge one: that is how long the cover takes to answer a raise."""
    stays = stays_from(_walk((10, PANEL), (16, NOTHING), (10, PANEL)))

    assert [round(stay.ms) for stay in stays] == [8, 8]


def test_a_second_window_taking_the_top_ends_the_first_ones_stay():
    stays = stays_from(_walk((10, PANEL), (10, OTHER)))

    assert [(stay.what, round(stay.ms)) for stay in stays] == [
        ("'panel' (hwnd=4242)", 8), ("'other' (hwnd=1717)", 8)]


def test_one_unbroken_stretch_is_one_stay():
    stays = stays_from(_walk((40, PANEL)))

    assert [round(stay.ms) for stay in stays] == [38]


def test_nothing_above_the_cover_is_no_stay_at_all():
    assert stays_from(_walk((40, NOTHING))) == []
    assert stays_from([]) == []


def test_a_pause_in_the_sampling_is_not_counted_into_a_stay():
    """A stretch nobody watched is not evidence the window stayed through it,
    so the stay ends at the pause rather than swallowing it.  Under-reporting
    is the right way to be wrong here: the alternative is failing a run over
    time the sampler never looked at."""
    samples = [(0.000, PANEL, "panel"), (0.002, PANEL, "panel"),
               (0.050, PANEL, "panel"), (0.052, PANEL, "panel")]

    assert [round(stay.ms) for stay in stays_from(samples)] == [2, 2]


def test_only_a_stay_past_the_budget_is_named():
    watcher = AboveTheCover()
    watcher.samples = _walk((VISIBLE_MS + 10, PANEL), (20, NOTHING), (10, PANEL))

    assert watcher.too_long() == ["'panel' (hwnd=4242) for 42ms"]


def test_the_budget_is_two_display_frames():
    """One frame at 60Hz is 16.7ms, and a window drawn for one of them is a
    flash the user sees.  The floor is one SetWindowPos — whatever puts a
    window back cannot run before the raise has happened — so it cannot be
    zero, but it is frames, not a number that happened to pass."""
    assert VISIBLE_MS == 34.0
