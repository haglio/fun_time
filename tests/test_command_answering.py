from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from shared_ui.message_shape import HEARD_MARK

from fun_time.bridge_records import Op, WindowOp
from fun_time.command_answering import AHK_PASSTHROUGH_OPS, LOOP_COMMANDS, OP_HANDLERS
from fun_time.command_value import parse_command
from fun_time.event_log import EventLogHandler, read_events
from fun_time.shared_state import BridgeState, read_shared_state
from fun_time.voice_commands import CommandLine
from tests.fun_time_window_fakes import HandDrivenClock
from tests.loop_parts import (
    WindowsThatWriteDownWhatTheyAreAsked,
    made_up_answering,
    made_up_window_work,
)


def _mailbox(tmp_path) -> list[str]:
    return (tmp_path / "ahk_cmd.txt").read_text(encoding="utf-8").split()


def test_a_command_of_the_loops_own_is_answered_without_the_dispatch(tmp_path):
    windows = WindowsThatWriteDownWhatTheyAreAsked()
    answering = made_up_answering(
        tmp_path, window_work=made_up_window_work(tmp_path, windows=windows))

    with patch("fun_time.command_answering.dispatch_command") as dispatch:
        answering.answer("omnirestore")

    dispatch.assert_not_called()
    assert windows.asked == [("restore_minimized",)]


def test_any_other_command_is_dispatched_and_what_comes_back_is_taken(tmp_path):
    windows = WindowsThatWriteDownWhatTheyAreAsked()
    answering = made_up_answering(
        tmp_path, window_work=made_up_window_work(tmp_path, windows=windows))
    changed = BridgeState(volume=40)

    with patch("fun_time.command_answering.dispatch_command",
               return_value=(changed, [WindowOp(op=Op.SHOW_WINDOW, key="portrait"),
                                       WindowOp(op=Op.SUSPEND_HOTKEYS)])) as dispatch:
        answering.answer("portrait_next", spoken_at=12.5)

    command, state_before, config = dispatch.call_args.args
    assert (command, state_before, config) == (
        parse_command("portrait_next"), BridgeState(), answering.config)
    assert dispatch.call_args.kwargs["given_at"] == 12.5
    assert answering.state == changed
    assert read_shared_state(tmp_path / "shared_state.ini") == changed
    assert windows.asked == [("show", "portrait")]
    assert _mailbox(tmp_path) == ["suspend_hotkeys"]


def test_entering_omnipause_is_kept_for_whatever_reads_the_shared_state(tmp_path):
    answering = made_up_answering(tmp_path)

    answering.answer("omnipause_toggle")

    assert answering.state.omni_paused
    assert read_shared_state(tmp_path / "shared_state.ini").omni_paused
    assert "suspend_hotkeys" in _mailbox(tmp_path)


def test_a_state_kept_is_the_state_the_next_reader_of_the_file_finds(tmp_path):
    answering = made_up_answering(tmp_path)

    answering.keep(BridgeState(mic_muted=True))

    assert answering.state.mic_muted
    assert read_shared_state(tmp_path / "shared_state.ini").mic_muted


def test_a_phrase_the_app_hears_as_a_lock_reaches_the_dispatch_in_its_config(tmp_path):
    answering = made_up_answering(tmp_path)

    answering.hear_as_a_lock(frozenset({"keep this one"}))

    assert answering.config.origenerator_lock_phrases == frozenset({"keep this one"})


class TestWhatALineSaidAloudFlashes:
    def _flashed(self, tmp_path, ops) -> list[tuple[str, int]]:
        answering = made_up_answering(tmp_path)
        with patch("fun_time.command_answering.dispatch_command",
                   return_value=(answering.state, ops)), \
             patch("fun_time.command_answering.flash_message") as flash:
            answering.handle_line(CommandLine("portrait_next", said="next", spoken_at=1.0))
        return [(call.args[1], call.kwargs["level"]) for call in flash.call_args_list]

    def test_a_line_nothing_else_answered_is_flashed_as_it_was_said(self, tmp_path):
        (message, _level), = self._flashed(tmp_path, [])

        assert message == "next"

    def test_a_line_the_dispatch_ignored_says_why(self, tmp_path):
        assert self._flashed(tmp_path, [WindowOp(op=Op.IGNORED, key="in Genau mode")]) == [
            ("ignored in Genau mode: next", logging.WARNING)]

    def test_a_line_answered_elsewhere_is_not_flashed_a_second_time(self, tmp_path):
        assert self._flashed(tmp_path, [WindowOp(op=Op.MAIN_FUNESTRA_ANSWERS)]) == []


class TestWhatALineSaidAloudIsMarkedWith:
    def _marks(self, tmp_path, caplog, line: CommandLine) -> list[str]:
        events = tmp_path / "event_log.jsonl"
        handler = EventLogHandler(events)
        logger = logging.getLogger("fun_time.command_answering")
        logger.addHandler(handler)
        answering = made_up_answering(tmp_path)
        try:
            with patch("fun_time.command_answering.dispatch_command",
                       return_value=(answering.state, [])):
                with caplog.at_level(logging.DEBUG, logger=logger.name):
                    answering.handle_line(line)
        finally:
            logger.removeHandler(handler)
        return [record.mark for record in read_events(events)[0] if record.message == "next"]

    def test_a_message_answering_a_line_he_said_starts_with_the_microphone(self, tmp_path,
                                                                            caplog):
        assert self._marks(tmp_path, caplog, CommandLine("portrait_next", said="next",
                                                         spoken_at=1.0)) == [HEARD_MARK]

    def test_a_message_answering_a_press_has_no_microphone(self, tmp_path, caplog):
        assert self._marks(tmp_path, caplog, CommandLine("portrait_next", said="next",
                                                         pressed_at=1.0)) == [""]


class TestTheDashboardsSnapshot:
    def test_a_locked_dashboard_state_file_warns_once_a_minute_not_never(self, tmp_path, caplog):
        answering = made_up_answering(tmp_path, dashboard_enabled=True)
        (tmp_path / "dashboard_state.ini").mkdir()
        with caplog.at_level(logging.WARNING, logger="fun_time.command_answering"):
            answering.update_dashboard()
            answering.update_dashboard()
        warnings = [r for r in caplog.records if "dashboard" in r.message]
        assert len(warnings) == 1

    def test_a_locked_dashboard_state_file_warns_again_a_minute_later(self, tmp_path, caplog):
        clock = HandDrivenClock()
        answering = made_up_answering(tmp_path, dashboard_enabled=True, clock=clock)
        (tmp_path / "dashboard_state.ini").mkdir()
        with caplog.at_level(logging.WARNING, logger="fun_time.command_answering"):
            answering.update_dashboard()
            clock.advance(61.0)
            answering.update_dashboard()
        warnings = [r for r in caplog.records if "dashboard" in r.message]
        assert len(warnings) == 2

    def test_a_bug_in_the_snapshot_writer_is_not_swallowed(self, tmp_path):
        answering = made_up_answering(tmp_path, dashboard_enabled=True)
        with patch(
            "fun_time.command_answering.write_dashboard_snapshot",
            side_effect=TypeError("renamed keyword"),
        ), pytest.raises(TypeError):
            answering.update_dashboard()

    def test_a_session_with_no_dashboard_writes_no_snapshot_when_a_command_is_taken(
            self, tmp_path):
        answering = made_up_answering(tmp_path, dashboard_enabled=False)

        answering.take(BridgeState(volume=40), [])

        assert not (tmp_path / "dashboard_state.ini").exists()


def test_the_op_interpreter_covers_the_whole_vocabulary():
    assert set(OP_HANDLERS) == set(Op)
    assert {Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS} == AHK_PASSTHROUGH_OPS


def test_an_unknown_op_is_an_error_not_an_ahk_verb(tmp_path, caplog):
    answering = made_up_answering(tmp_path)

    with caplog.at_level(logging.ERROR, logger="fun_time.command_answering"):
        answering.take(answering.state, [WindowOp(op="frobnicate")])

    assert not (tmp_path / "ahk_cmd.txt").exists()
    assert any("frobnicate" in record.message for record in caplog.records)


def test_quit_is_one_of_the_loops_own_commands():
    assert "quit" in LOOP_COMMANDS
