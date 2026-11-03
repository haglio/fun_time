from __future__ import annotations

from types import SimpleNamespace

from funestra.funestra import Channels, Funestra
from funestra.playlist import read_playlist
from funestra.testing import FakeEngine

from main_funestra.window import run_the_window, run_until_stopped

WINDOW = (640, 480)
BATCH = ["a press"]


class _Spy:
    def __init__(self, *, passes: int) -> None:
        self._passes_left = passes
        self.happened: list[tuple] = []

    @property
    def stopped(self) -> bool:
        return self._passes_left == 0

    def window_size(self):
        self.happened.append(("measured",))
        return WINDOW

    def events(self):
        self.happened.append(("events",))
        return BATCH

    def deal(self, events, viewport) -> None:
        self.happened.append(("dealt", events, viewport))

    def tick(self, *, viewport) -> None:
        self.happened.append(("ticked", viewport))
        self._passes_left -= 1

    def pace(self) -> None:
        self.happened.append(("paced",))

    def close(self) -> None:
        self.happened.append(("closed",))


def _run(spy: _Spy) -> None:
    run_until_stopped(spy, window_size=spy.window_size, events=spy.events, deal=spy.deal, pace=spy.pace)


def test_each_pass_measures_the_window_deals_the_events_then_ticks_then_paces():
    spy = _Spy(passes=1)

    _run(spy)

    assert spy.happened == [("measured",), ("events",), ("dealt", BATCH, WINDOW), ("ticked", WINDOW),
                            ("paced",), ("closed",)]


def test_it_goes_round_until_the_funestra_has_stopped():
    spy = _Spy(passes=3)

    _run(spy)

    assert [step[0] for step in spy.happened].count("ticked") == 3


def test_a_funestra_stopped_before_the_first_pass_is_only_closed():
    spy = _Spy(passes=0)

    _run(spy)

    assert spy.happened == [("closed",)]


def _a_funestra(tmp_path) -> tuple[Funestra, FakeEngine]:
    video = tmp_path / "one.mp4"
    video.write_bytes(b"")
    playlist = tmp_path / "playlist.tsv"
    playlist.write_text(f"{video}\n", encoding="utf-8")
    channels = Channels(playlist=playlist, command=tmp_path / "cmd.txt",
                        paused=tmp_path / "paused.txt", status=tmp_path / "status.txt")
    engine = FakeEngine()
    return Funestra(engine, channels=channels, playlist=read_playlist(playlist)), engine


def test_a_close_asked_of_a_real_funestra_ends_the_loop_and_closes_its_engine(tmp_path):
    funestra, engine = _a_funestra(tmp_path)

    run_until_stopped(funestra, window_size=lambda: WINDOW, events=lambda: BATCH,
                      deal=lambda _events, _viewport: funestra.close_requested(), pace=lambda: None)

    assert (funestra.stopped, engine.closed) == (True, True)


class _Pygame:
    def __init__(self) -> None:
        self.ticked_at: list[int] = []
        self.quit_called = 0
        self.display = SimpleNamespace(get_window_size=lambda: WINDOW)
        self.event = SimpleNamespace(get=lambda: BATCH)
        self.time = SimpleNamespace(Clock=lambda: SimpleNamespace(tick=self.ticked_at.append))

    def quit(self) -> None:
        self.quit_called += 1


def test_on_pygame_the_loop_reads_its_window_and_events_paces_sixty_times_a_second_and_quits():
    spy = _Spy(passes=2)
    pygame = _Pygame()

    run_the_window(pygame, spy, spy.deal)

    assert [step for step in spy.happened if step[0] == "dealt"] == [("dealt", BATCH, WINDOW)] * 2
    assert (pygame.ticked_at, pygame.quit_called) == ([60, 60], 1)
    assert spy.happened[-1] == ("closed",)
