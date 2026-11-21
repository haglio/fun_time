from __future__ import annotations

from pathlib import Path

from app_support.threading_utils import wait_until

from fun_time.loopback_inbox import LoopbackInbox, post_to_inbox
from tests.standing_files import left_to_stand


def _counting_reads_of(port_file: Path, monkeypatch) -> list[Path]:
    reads: list[Path] = []
    real_read = Path.read_text

    def counted(path, *args, **kwargs):
        if path == port_file:
            reads.append(path)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    return reads


def test_each_press_echoed_reads_the_port_file_only_when_it_changed(tmp_path, monkeypatch):
    port_file = tmp_path / "dashboard_press_port.txt"
    inbox = LoopbackInbox(port_file, listener_name="test-press-listener")
    left_to_stand(port_file)
    try:
        reads = _counting_reads_of(port_file, monkeypatch)

        for press in ("alpha", "beta", "gamma"):
            assert post_to_inbox(port_file, press)

        heard: list[str] = []
        wait_until(lambda: heard.extend(inbox.take_all()) or len(heard) == 3)
        assert (heard, len(reads)) == (["alpha", "beta", "gamma"], 1)
    finally:
        inbox.stop()


def test_a_press_echoed_before_the_dashboard_listens_finds_it_once_it_does(tmp_path):
    port_file = tmp_path / "dashboard_press_port.txt"

    assert post_to_inbox(port_file, "alpha") is False
    inbox = LoopbackInbox(port_file, listener_name="test-press-listener")
    try:
        assert post_to_inbox(port_file, "beta") is True
        heard: list[str] = []
        wait_until(lambda: heard.extend(inbox.take_all()) or len(heard) == 1)
        assert heard == ["beta"]
    finally:
        inbox.stop()
