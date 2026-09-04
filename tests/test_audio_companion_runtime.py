from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from fun_time.audio_companion_runtime import AudioCompanionRuntime


def test_process_iteration_applies_pause_command_on_socket_timeout(tmp_path: Path):
    sock = Mock()
    sock.recvfrom.side_effect = TimeoutError()
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
        volume_file=tmp_path / "volume.txt",
        read_volume=Mock(return_value=100),
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
        volume_file=tmp_path / "volume.txt",
        read_volume=Mock(return_value=100),
    )

    runtime.process_iteration()

    controller.set_mode_active.assert_called_once_with(True)
    controller.set_manual_paused.assert_called_once_with(False)
    controller.handle_udp_line.assert_called_once_with("VISIBLE 1")


def test_process_iteration_applies_the_published_volume(tmp_path: Path):
    """The bridge owns the sound level and publishes it to a file; the companion
    polls it beside the pause flag so a restarted companion re-reads it."""
    sock = Mock()
    sock.recvfrom.side_effect = TimeoutError()
    controller = Mock()
    runtime = AudioCompanionRuntime(
        sock=sock,
        controller=controller,
        mode_file=tmp_path / "mode.txt",
        read_mode_active=Mock(return_value=True),
        paused_file=tmp_path / "paused.txt",
        read_paused_state=Mock(return_value=False),
        volume_file=tmp_path / "volume.txt",
        read_volume=Mock(return_value=30),
    )

    runtime.process_iteration()

    controller.set_volume.assert_called_once_with(30)


def test_close_closes_socket(tmp_path: Path):
    sock = Mock()
    runtime = AudioCompanionRuntime(
        sock=sock,
        controller=Mock(),
        mode_file=tmp_path / "mode.txt",
        read_mode_active=Mock(return_value=False),
        paused_file=tmp_path / "paused.txt",
        read_paused_state=Mock(return_value=False),
        volume_file=tmp_path / "volume.txt",
        read_volume=Mock(return_value=100),
    )

    runtime.close()

    sock.close.assert_called_once_with()
