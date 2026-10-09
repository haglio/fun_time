from __future__ import annotations

import sys
import time

import pytest

from fun_time.win32_process import is_process_alive

from .integration_support import (
    COMMAND_BUDGET_S,
    QUIT_BUDGET_S,
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
    the_runs_tcode_sink,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)


def _moves_waiting(sink) -> int:
    moves = 0
    while True:
        try:
            sink.recv(65536)
        except BlockingIOError:
            return moves
        moves += 1


def test_quitting_stops_the_robot_hand_before_anything_closes_and_parks_the_osr2():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start(env_overrides={"FUN_TIME_INTEGRATION_OVERLAYS": "1"})
        sink = the_runs_tcode_sink(session.config.vr.tcode_udp_port)
        sink.setblocking(False)
        session.write_dashboard_command("genau_activate")
        session.wait_for_new_log("Switched to genau mode", timeout=COMMAND_BUDGET_S)
        _moves_waiting(sink)
        session.wait_until(lambda: _moves_waiting(sink) > 0, timeout=COMMAND_BUDGET_S,
                           description="the Robot Hand to drive the OSR2")
        main_player = session.read_child_processes()["main_player_pid"]
        session.config.broker_cmd_file.unlink(missing_ok=True)

        session.write_dashboard_command("quit")
        last_move_at = time.monotonic()
        deadline = last_move_at + QUIT_BUDGET_S
        while is_process_alive(main_player.pid):
            assert time.monotonic() < deadline, (
                f"the Main Player was still running {QUIT_BUDGET_S:g}s after the quit\n"
                f"{session._log_tail()}")
            if _moves_waiting(sink):
                last_move_at = time.monotonic()
            time.sleep(0.01)
        quiet_before_the_main_player_went = time.monotonic() - last_move_at
        session._proc.wait(timeout=QUIT_BUDGET_S)

        assert quiet_before_the_main_player_went >= 0.25, (
            f"the Robot Hand drove the OSR2 until {quiet_before_the_main_player_went:.2f}s "
            f"before the Main Player it runs in was killed, so the kill stopped it and the "
            f"quit did not\n{session._log_tail()}")
        assert session.config.broker_cmd_file.read_text(encoding="utf-8") == "PARK"
    finally:
        session.stop()
