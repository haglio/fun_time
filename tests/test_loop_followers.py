from __future__ import annotations

from dataclasses import replace

from funestra.modes import MainMode

from fun_time.calls_in_turn import AtOnce
from fun_time.dashboard_actions import ENTER_OMNIPAUSE
from fun_time.dispatch_loop_setup import Publishing, SessionStart
from fun_time.fun_time_windows import ChildPids, FunTimeWindows
from fun_time.loop_followers import (
    ClicksIntoATextField,
    EachTick,
    Every,
    TheBrowsersSlideshow,
    poll_dashboard_commands,
    the_followers,
)
from fun_time.rfb_modes import RfbMode
from fun_time.shared_state import BridgeState
from fun_time.windows_bridge_dispatch_loop import DispatchLoopRunner
from tests.bridge_configs import made_up_bridge_config
from tests.fun_time_window_fakes import HandDrivenClock
from tests.loop_parts import made_up_answering, made_up_session_files


class _WritesDownEachTick:
    def __init__(self, name: str = "", into: list | None = None) -> None:
        self.name = name
        self.ticks: list = [] if into is None else into

    def tick(self, now, answering) -> None:
        self.ticks.append((self.name, now, answering.state.volume))


def _a_loop(tmp_path, followers, clock=None) -> DispatchLoopRunner:
    return DispatchLoopRunner(
        made_up_bridge_config(tmp_path), made_up_session_files(tmp_path),
        SessionStart(windows=FunTimeWindows(pids=ChildPids(), calls=AtOnce())),
        Publishing(dashboard_enabled=False),
        followers=followers, clock=clock or HandDrivenClock())


def test_a_follower_added_to_the_list_is_run_by_the_loop_with_the_app_as_it_stands(tmp_path):
    added = _WritesDownEachTick("a new follower")
    clock = HandDrivenClock()
    loop = _a_loop(tmp_path, lambda parts: (*the_followers(parts), added), clock)
    loop.answering.keep(replace(loop.answering.state, volume=35))

    loop.tick()
    clock.advance(0.05)
    loop.tick()

    assert added.ticks == [("a new follower", 1000.0, 35), ("a new follower", 1000.05, 35)]


def test_the_loop_runs_its_followers_in_the_order_they_are_listed(tmp_path):
    ran: list = []
    loop = _a_loop(tmp_path, lambda parts: [_WritesDownEachTick("first", ran),
                                           _WritesDownEachTick("second", ran)])

    loop.tick()

    assert [name for name, _now, _volume in ran] == ["first", "second"]


def test_a_follower_sees_what_the_one_before_it_changed(tmp_path):
    seen = _WritesDownEachTick()
    turns_it_down = EachTick(lambda now, answering: answering.keep(
        replace(answering.state, volume=10)))
    loop = _a_loop(tmp_path, lambda parts: [turns_it_down, seen])

    loop.tick()

    assert [volume for _name, _now, volume in seen.ticks] == [10]


class TestEvery:
    def test_its_followers_run_once_each_time_the_interval_has_passed(self, tmp_path):
        paced = _WritesDownEachTick()
        every = Every(0.2, [paced])
        answering = made_up_answering(tmp_path)

        for now in (1000.0, 1000.1, 1000.19, 1000.2, 1000.3, 1000.45):
            every.tick(now, answering)

        assert [now for _name, now, _volume in paced.ticks] == [1000.0, 1000.2, 1000.45]


class _ATypingWatch:
    def __init__(self, clicked: bool) -> None:
        self._clicked = clicked
        self.asked_at: list[float] = []

    def clicked_into_a_text_field(self, *, now: float) -> bool:
        self.asked_at.append(now)
        return self._clicked


class _AnsweringThatWritesDownWhatItIsAsked:
    def __init__(self, state: BridgeState) -> None:
        self.state = state
        self.answered: list[str] = []

    def answer(self, command: str) -> None:
        self.answered.append(command)


class TestClicksIntoATextField:
    def _answered(self, *, clicked: bool, paused: bool) -> list[str]:
        answering = _AnsweringThatWritesDownWhatItIsAsked(BridgeState(omni_paused=paused))
        ClicksIntoATextField(_ATypingWatch(clicked)).tick(5.0, answering)
        return answering.answered

    def test_a_click_into_a_text_field_enters_omnipause(self):
        assert self._answered(clicked=True, paused=False) == [ENTER_OMNIPAUSE]

    def test_a_click_while_already_paused_asks_for_nothing(self):
        assert self._answered(clicked=True, paused=True) == []

    def test_no_click_asks_for_nothing(self):
        assert self._answered(clicked=False, paused=False) == []

    def test_the_watch_is_asked_at_the_loops_own_time(self):
        typing = _ATypingWatch(clicked=False)

        ClicksIntoATextField(typing).tick(5.0, _AnsweringThatWritesDownWhatItIsAsked(BridgeState()))

        assert typing.asked_at == [5.0]


class _ASlideshow:
    def __init__(self) -> None:
        self.ticks: list[tuple[float, bool]] = []

    def tick(self, *, now: float, held: bool) -> None:
        self.ticks.append((now, held))


class TestTheBrowsersSlideshow:
    def _held(self, state: BridgeState) -> bool:
        slideshow = _ASlideshow()
        TheBrowsersSlideshow(slideshow).tick(7.0, _AnsweringThatWritesDownWhatItIsAsked(state))
        (now, held), = slideshow.ticks
        assert now == 7.0
        return held

    def test_it_turns_while_the_browser_shows_and_nothing_is_paused(self):
        assert not self._held(BridgeState())

    def test_omnipause_holds_it(self):
        assert self._held(BridgeState(omni_paused=True))

    def test_origenerator_core_in_the_browsers_place_holds_it(self):
        assert self._held(BridgeState(rfb_mode=RfbMode.ORIGENERATOR))


class TestPollDashboardCommands:
    def test_reads_and_deletes_command_file(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("portrait_next", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["portrait_next"]
        assert not cmd_file.exists()

    def test_a_verb_carrying_a_path_keeps_the_paths_case(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("play_file C:/Videos/Alpha/Scene One.MP4", encoding="utf-8")

        assert poll_dashboard_commands(cmd_file) == ["play_file C:/Videos/Alpha/Scene One.MP4"]

    def test_a_command_file_that_is_not_there_holds_no_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"

        result = poll_dashboard_commands(cmd_file)

        assert result == []

    def test_a_command_file_nobody_has_written_to_holds_no_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == []

    def test_strips_whitespace(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("  landscape_lock  \n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["landscape_lock"]

    def test_reads_multiple_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("main_next\nmain_next\nportrait_prev\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["main_next", "main_next", "portrait_prev"]

    def test_concurrent_write_does_not_lose_new_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_text("first_command\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["first_command"]
        cmd_file.write_text("second_command\n", encoding="utf-8")
        assert cmd_file.exists()

    def test_a_stale_claim_file_does_not_block_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        stale = cmd_file.with_suffix(cmd_file.suffix + ".consuming")
        stale.write_text("old_command\n", encoding="utf-8")
        cmd_file.write_text("new_command\n", encoding="utf-8")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["new_command"]
        assert not cmd_file.exists()
        assert not stale.exists()

    def test_strips_utf8_bom_from_commands(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        cmd_file.write_bytes(b"\xef\xbb\xbfmain_prev\n")

        result = poll_dashboard_commands(cmd_file)

        assert result == ["main_prev"]


def test_the_loop_brings_genau_up_once_it_says_the_flick_the_gallery_asked_for_is_whole(tmp_path):
    loop = _a_loop(tmp_path, the_followers)
    flick = tmp_path / "flicks" / "flick one.mp4"
    loop.answering.keep(replace(loop.answering.state, main_mode=MainMode.KINO,
                                genau_waits_for=str(flick)))
    status = loop.answering.config.genau.status_file
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(f"locked=1\nflick={flick}\nwhole=0\n", encoding="utf-8")

    loop.tick()
    assert loop.answering.state.main_mode is MainMode.KINO

    status.write_text(f"locked=1\nflick={flick}\nwhole=1\n", encoding="utf-8")
    loop.tick()
    assert loop.answering.state.main_mode is MainMode.GENAU
