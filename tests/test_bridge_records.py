from __future__ import annotations

from pathlib import Path

from fun_time.bridge_records import BridgeConfig, Op, WindowOp
from fun_time.config import SatelliteChannel
from fun_time.fun_time_funestras import FunTimeFunestra
from tests.bridge_configs import made_up_bridge_config


def _make_config(tmp_path: Path) -> BridgeConfig:
    state_dir = tmp_path / "state"
    return made_up_bridge_config(
        state_dir,
        trash_dir=tmp_path / "trash",
        main_sources=str(tmp_path / "primary"),
    )


def test_each_satellite_is_asked_for_by_its_funestra(tmp_path: Path):
    config = _make_config(tmp_path)

    assert config.satellite(FunTimeFunestra.PORTRAIT) is config.portrait
    assert config.satellite(FunTimeFunestra.LANDSCAPE) is config.landscape
    assert config.satellite(2) is config.portrait


def test_a_satellites_channel_is_spelled_once_from_its_state_dir(tmp_path: Path):
    state_dir = tmp_path / "state"

    channel = SatelliteChannel.in_state_dir(state_dir, FunTimeFunestra.LANDSCAPE)

    assert channel == SatelliteChannel(
        cmd_file=state_dir / "landscape_cmd.txt",
        paused_file=state_dir / "landscape_paused.txt",
        status_file=state_dir / "landscape_status.txt",
        playlist_file=state_dir / "landscape_playlist.tsv",
        notice_file=state_dir / "landscape_notice.txt",
        hud_file=state_dir / "landscape_hud.json",
        origenerator_hud_file=state_dir / "origenerator_landscape_hud.json",
    )


def test_a_window_op_written_as_its_plain_string_is_the_op_itself():
    assert WindowOp(op="message").op == Op.MESSAGE
    assert {Op.MESSAGE: "handled"}["message"] == "handled"
