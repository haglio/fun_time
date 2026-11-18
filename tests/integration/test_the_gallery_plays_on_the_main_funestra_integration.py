from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fun_time.funestra_status import read_genau_status
from fun_time.mode_switches import PLAY_IN_GENAU, PLAY_IN_KINO

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
    published_status,
    video_of_a_test_pattern,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)


@pytest.fixture(scope="module")
def session_with_videos_the_gallery_holds():
    temp_root = build_integration_temp_root()
    session = FunTimeIntegrationSession(build_integration_config(temp_root))
    handed_over = temp_root / "handed over by the gallery"
    handed_over.mkdir()
    try:
        session.start()
        yield session, handed_over
    finally:
        session.stop()


def _same_file(shown: str, video: Path) -> bool:
    return bool(shown) and Path(shown) == video


def test_a_video_the_gallery_hands_over_plays_on_the_main_funestra(
        session_with_videos_the_gallery_holds):
    session, handed_over = session_with_videos_the_gallery_holds
    video = video_of_a_test_pattern(handed_over / "video one.mp4", size="640x360", seconds=6)
    session.write_dashboard_command("genau_activate")
    session.wait_for_new_log("Switched to genau mode")

    session.write_dashboard_command(f"{PLAY_IN_KINO}|{video}")

    session.wait_for_new_log("Switched to kino mode")
    session.wait_until(lambda: _same_file(session.read_main_funestra_status().video, video),
                       description="the Main Funestra to play the video the gallery handed over")


def test_a_flick_the_gallery_hands_over_plays_in_genau(session_with_videos_the_gallery_holds):
    session, handed_over = session_with_videos_the_gallery_holds
    flick = video_of_a_test_pattern(handed_over / "flick one.mp4", size="640x360", seconds=3)
    genau_status = session.config.genau.status_file

    session.write_dashboard_command(f"{PLAY_IN_GENAU}|{flick}")

    session.wait_for_new_log("Switched to genau mode")
    session.wait_until(
        lambda: _same_file(published_status(read_genau_status, genau_status).flick, flick),
        description="Genau to play the flick the gallery handed over")
