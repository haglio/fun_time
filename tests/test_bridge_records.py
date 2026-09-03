"""BridgeConfig's per-side view — the config half of the side lens."""
from __future__ import annotations

from pathlib import Path

from fun_time.bridge_records import BridgeConfig


def _make_config(tmp_path: Path) -> BridgeConfig:
    state_dir = tmp_path / "state"
    return BridgeConfig(
        portrait_cmd_file=state_dir / "portrait_cmd.txt",
        portrait_paused_file=state_dir / "portrait_paused.txt",
        portrait_status_file=state_dir / "portrait_status.txt",
        portrait_playlist_file=state_dir / "portrait_playlist.tsv",
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        landscape_playlist_file=state_dir / "landscape_playlist.tsv",
        favs_file=tmp_path / "favs.csv",
        weird_dir=tmp_path / "weird",
        state_dir=state_dir,
        main_sources=str(tmp_path / "primary"),
        portrait_sources=str(tmp_path / "portrait"),
        landscape_sources=str(tmp_path / "landscape"),
        genau_mode_file=state_dir / "genau_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        audio_volume_file=state_dir / "audio_volume.txt",
        nau_cmd_file=state_dir / "nau_cmd.txt",
        nau_paused_file=state_dir / "nau_paused.txt",
        nau_status_file=state_dir / "nau_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
    )


def test_side_bundles_one_satellites_channel(tmp_path: Path):
    config = _make_config(tmp_path)

    portrait, landscape = config.side(2), config.side(3)

    assert portrait.cmd_file == config.portrait_cmd_file
    assert portrait.paused_file == config.portrait_paused_file
    assert portrait.status_file == config.portrait_status_file
    assert portrait.playlist_file == config.portrait_playlist_file
    assert portrait.sources == config.portrait_sources
    assert landscape.cmd_file == config.landscape_cmd_file
    assert landscape.paused_file == config.landscape_paused_file
    assert landscape.status_file == config.landscape_status_file
    assert landscape.playlist_file == config.landscape_playlist_file
    assert landscape.sources == config.landscape_sources
