from __future__ import annotations

from pathlib import Path

from fun_time.robot_hand.notifier import RobotHandNotifier


class FakeSocket:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        self.closed = True


def test_notify_clip_sends_clip_stem():
    sock = FakeSocket()
    notifier = RobotHandNotifier("127.0.0.1", 9999, sock=sock)

    notifier.notify_clip(Path("demo.mp4"))

    assert sock.sent == [(b"CLIP demo", ("127.0.0.1", 9999))]


def test_notify_visible_deduplicates_repeated_state():
    sock = FakeSocket()
    notifier = RobotHandNotifier("127.0.0.1", 9999, sock=sock)

    notifier.notify_visible(True)
    notifier.notify_visible(True)
    notifier.notify_visible(False)

    assert sock.sent == [
        (b"VISIBLE 1", ("127.0.0.1", 9999)),
        (b"VISIBLE 0", ("127.0.0.1", 9999)),
    ]


def test_sync_window_visibility_resends_clip_when_showing_from_hidden_state():
    sock = FakeSocket()
    notifier = RobotHandNotifier("127.0.0.1", 9999, sock=sock)
    shown: list[str] = []
    hidden: list[str] = []

    result = notifier.sync_window_visibility(
        desired_visible=True,
        window_visible=False,
        current_clip_path=Path("demo.mp4"),
        show_window=lambda: shown.append("show"),
        hide_window=lambda: hidden.append("hide"),
    )

    assert result is True
    assert shown == ["show"]
    assert hidden == []
    assert sock.sent == [
        (b"CLIP demo", ("127.0.0.1", 9999)),
        (b"VISIBLE 1", ("127.0.0.1", 9999)),
    ]


def test_sync_window_visibility_hides_window_when_visibility_turns_off():
    sock = FakeSocket()
    notifier = RobotHandNotifier("127.0.0.1", 9999, sock=sock)
    notifier.last_visible_sent = 1
    shown: list[str] = []
    hidden: list[str] = []

    result = notifier.sync_window_visibility(
        desired_visible=False,
        window_visible=True,
        current_clip_path=Path("demo.mp4"),
        show_window=lambda: shown.append("show"),
        hide_window=lambda: hidden.append("hide"),
    )

    assert result is False
    assert shown == []
    assert hidden == ["hide"]
    assert sock.sent == [(b"VISIBLE 0", ("127.0.0.1", 9999))]


def test_sync_window_visibility_is_noop_when_state_is_unchanged():
    sock = FakeSocket()
    notifier = RobotHandNotifier("127.0.0.1", 9999, sock=sock)

    result = notifier.sync_window_visibility(
        desired_visible=False,
        window_visible=False,
        current_clip_path=Path("demo.mp4"),
        show_window=lambda: None,
        hide_window=lambda: None,
    )

    assert result is False
    assert sock.sent == []


def test_close_closes_socket():
    sock = FakeSocket()
    notifier = RobotHandNotifier("127.0.0.1", 9999, sock=sock)

    notifier.close()

    assert sock.closed is True
