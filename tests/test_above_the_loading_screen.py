from __future__ import annotations

from tests.integration.above_the_loading_screen import VISIBLE_MS, AboveTheLoadingScreen, stays_from

PANEL, OTHER, NOTHING = 4242, 1717, 0
POLL_MS = 2.0


def _walk(*spans: tuple[float, int]) -> list[tuple[float, int, str]]:
    samples: list[tuple[float, int, str]] = []
    now = 0.0
    for ms, hwnd in spans:
        end = now + ms
        while now < end:
            samples.append((now / 1000, hwnd, {PANEL: "panel", OTHER: "other"}.get(hwnd, "")))
            now += POLL_MS
    return samples


def test_the_covers_own_answer_ends_a_stay():
    stays = stays_from(_walk((10, PANEL), (16, NOTHING), (10, PANEL)))

    assert [round(stay.ms) for stay in stays] == [8, 8]


def test_a_second_window_taking_the_top_ends_the_first_ones_stay():
    stays = stays_from(_walk((10, PANEL), (10, OTHER)))

    assert [(stay.what, round(stay.ms)) for stay in stays] == [
        ("'panel' (hwnd=4242)", 8), ("'other' (hwnd=1717)", 8)]


def test_one_unbroken_stretch_is_one_stay():
    stays = stays_from(_walk((40, PANEL)))

    assert [round(stay.ms) for stay in stays] == [38]


def test_nothing_above_the_loading_screen_is_no_stay_at_all():
    assert stays_from(_walk((40, NOTHING))) == []
    assert stays_from([]) == []


def test_a_pause_in_the_sampling_is_not_counted_into_a_stay():
    samples = [(0.000, PANEL, "panel"), (0.002, PANEL, "panel"),
               (0.050, PANEL, "panel"), (0.052, PANEL, "panel")]

    assert [round(stay.ms) for stay in stays_from(samples)] == [2, 2]


def test_only_a_stay_past_the_budget_is_named():
    watcher = AboveTheLoadingScreen()
    watcher.samples = _walk((VISIBLE_MS + 10, PANEL), (20, NOTHING), (10, PANEL))

    assert watcher.too_long() == ["'panel' (hwnd=4242) for 42ms"]


def test_the_budget_is_two_display_frames():
    assert VISIBLE_MS == 34.0
