import logging
import threading

from fun_time.window_waits import THE_LOOPS_THREAD, WindowWaits

LOGGER = "fun_time.window_waits"


class WoundClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _waits(clock: WoundClock) -> WindowWaits:
    return WindowWaits(clock=clock)


def test_a_wait_longer_than_a_frame_is_written_down_with_the_call_that_made_it(caplog):
    clock = WoundClock()
    waits = _waits(clock)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        waits.waited("set_always_on_top(4242, True)", 0.250, answered=True)

    assert caplog.messages == [(
        "Slow window call: set_always_on_top(4242, True) waited 250.0 ms; "
        "1 slow call(s) since the last line")]


def test_a_quick_wait_writes_nothing_until_the_minute_is_up(caplog):
    clock = WoundClock()
    waits = _waits(clock)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(50):
            waits.waited("place_window(7)", 0.002, answered=True)

    assert caplog.messages == []


def test_slow_waits_in_one_second_make_one_line_naming_the_worst(caplog):
    clock = WoundClock()
    waits = _waits(clock)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        waits.waited("place_window(1)", 0.030, answered=True)
        clock.now += 0.2
        waits.waited("place_window(2)", 0.400, answered=True)
        clock.now += 0.2
        waits.waited("place_window(3)", 0.050, answered=True)
        clock.now += 1.0
        waits.waited("place_window(4)", 0.001, answered=True)

    assert caplog.messages == [
        "Slow window call: place_window(1) waited 30.0 ms; 1 slow call(s) since the last line",
        "Slow window call: place_window(2) waited 400.0 ms; 2 slow call(s) since the last line",
    ]


def test_each_minute_with_calls_is_summed_up(caplog):
    clock = WoundClock()
    waits = _waits(clock)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        waits.waited("place_window(1)", 0.002, answered=True)
        waits.waited("minimize_window(2)", 0.150, answered=True)
        clock.now += 30
        waits.waited("set_always_on_top(3, True)", 1.5, answered=False)
        clock.now += 31
        waits.waited("place_window(1)", 0.001, answered=True)

    assert caplog.messages[-1] == (
        "Window calls over 61 s: 4 to other programs' windows, 2 over 16.7 ms, "
        "2 over 100 ms, 1 given up on, 1653.0 ms waited in all, "
        "worst 1500.0 ms (set_always_on_top(3, True)); 0.0 ms of it held Fun Time's loop")


def test_a_slow_call_says_which_of_the_apps_windows_it_was_for(caplog):
    clock = WoundClock()
    waits = _waits(clock)
    waits.call_it(4242, "the Main Funestra")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        waits.waited("set_always_on_top(4242, True)", 0.250, answered=True, hwnd=4242)
        clock.now += 61
        waits.waited("place_window(7)", 0.001, answered=True, hwnd=7)

    assert caplog.messages == [
        "Slow window call: set_always_on_top(4242, True) on the Main Funestra waited 250.0 ms; "
        "1 slow call(s) since the last line",
        "Window calls over 61 s: 2 to other programs' windows, 1 over 16.7 ms, "
        "1 over 100 ms, 0 given up on, 251.0 ms waited in all, "
        "worst 250.0 ms (set_always_on_top(4242, True) on the Main Funestra); "
        "0.0 ms of it held Fun Time's loop"]


def test_the_minute_says_how_much_of_the_waiting_held_fun_times_loop(caplog):
    clock = WoundClock()
    asked_on_the_loop = [True]
    waits = WindowWaits(clock=clock, on_the_loop=lambda: asked_on_the_loop[0])

    with caplog.at_level(logging.INFO, logger=LOGGER):
        waits.waited("place_window(1)", 0.200, answered=True)
        asked_on_the_loop[0] = False
        waits.waited("place_window(2)", 0.300, answered=True)
        clock.now += 61
        waits.waited("place_window(3)", 0.0, answered=True)

    assert caplog.messages[-1].endswith(
        "500.0 ms waited in all, worst 300.0 ms (place_window(2)); "
        "200.0 ms of it held Fun Time's loop")


def test_a_call_asked_for_on_the_loops_own_thread_is_counted_as_holding_it():
    waits = WindowWaits()
    seen: list[bool] = []
    on_the_loop = threading.Thread(target=lambda: seen.append(waits.on_the_loop()),
                                   name=THE_LOOPS_THREAD)

    on_the_loop.start()
    on_the_loop.join()

    assert seen == [True]
    assert waits.on_the_loop() is False
