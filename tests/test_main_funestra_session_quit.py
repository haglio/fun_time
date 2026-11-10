from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pygame
from funestra.funestra import Channels, Funestra
from funestra.funestra_setup import FunestraSetup
from funestra.playlist import PlaylistItem
from funestra.session_quit import SESSION_QUIT
from funestra.testing import FakeEngine

from main_funestra.input import Input

REPO = Path(__file__).resolve().parents[1]

THE_CALL_EACH_LINK_OF_A_CLOSE_MAKES = {
    REPO / "main_funestra" / "window.py": "deal",
    REPO / "main_funestra" / "input.py": "close_requested",
}


def _calls(source: Path, name: str) -> bool:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(tree)
    )


def _closed_from_its_window(tmp_path, channels: Channels) -> Funestra:
    video = tmp_path / "video one.mp4"
    video.write_bytes(b"")
    funestra = Funestra(FakeEngine(), FunestraSetup(channels=channels, playlist=[PlaylistItem(video)]))
    Input(funestra).deal([SimpleNamespace(type=pygame.QUIT)], (640, 480))
    return funestra


def test_a_close_inside_a_session_asks_fun_time_to_quit_and_leaves_this_window_up(tmp_path):
    fun_times_commands = tmp_path / "dashboard_cmd.txt"

    funestra = _closed_from_its_window(tmp_path, Channels(dashboard_cmd=fun_times_commands))

    assert not funestra.stopped
    assert fun_times_commands.read_text(encoding="utf-8").split() == [SESSION_QUIT]


def test_a_close_with_no_session_around_it_ends_the_one_window_there_is(tmp_path):
    assert _closed_from_its_window(tmp_path, Channels()).stopped


def test_a_close_passes_both_links_from_the_loop_to_the_session():
    bypassed = [
        source.relative_to(REPO).as_posix()
        for source, call in THE_CALL_EACH_LINK_OF_A_CLOSE_MAKES.items()
        if not _calls(source, call)
    ]

    assert not bypassed, (
        f"{bypassed} no longer hand a close on, and a window that ends itself "
        "leaves the session running around a gap nothing refills"
    )
