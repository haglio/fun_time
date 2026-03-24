from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import Mock

from fun_time.audio_companion_runtime import AudioCompanionRuntime


def test_process_iteration_applies_pause_command_on_socket_timeout(tmp_path: Path):
    sock = Mock()
    sock.recvfrom.side_effect = socket.timeout()
    controller = Mock()
    read_mode_active = Mock(return_value=False)
    read_paused_state = Mock(return_value=True)
    runtime = AudioCompanionRuntime(
        sock=sock,
        controller=controller,
        mode_file=tmp_path / "mode.txt",
        read_mode_active=read_mode_active,
        paused_file=tmp_path / "paused.txt",
        read_paused_state=read_paused_state,
    )

    runtime.process_iteration()

    controller.set_mode_active.assert_called_once_with(False)
    controller.set_manual_paused.assert_called_once_with(True)
    controller.handle_udp_line.assert_not_called()


def test_process_iteration_handles_udp_line_after_runtime_command(tmp_path: Path):
    sock = Mock()
    sock.recvfrom.return_value = (b"VISIBLE 1\n", ("127.0.0.1", 9999))
    controller = Mock()
    read_mode_active = Mock(return_value=True)
    read_paused_state = Mock(return_value=False)
    runtime = AudioCompanionRuntime(
        sock=sock,
        controller=controller,
        mode_file=tmp_path / "mode.txt",
        read_mode_active=read_mode_active,
        paused_file=tmp_path / "paused.txt",
        read_paused_state=read_paused_state,
    )

    runtime.process_iteration()

    controller.set_mode_active.assert_called_once_with(True)
    controller.set_manual_paused.assert_called_once_with(False)
    controller.handle_udp_line.assert_called_once_with("VISIBLE 1")


def test_close_closes_socket(tmp_path: Path):
    sock = Mock()
    runtime = AudioCompanionRuntime(
        sock=sock,
        controller=Mock(),
        mode_file=tmp_path / "mode.txt",
        read_mode_active=Mock(return_value=False),
        paused_file=tmp_path / "paused.txt",
        read_paused_state=Mock(return_value=False),
    )

    runtime.close()

    sock.close.assert_called_once_with()
