from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.satellite_launch import SatelliteLaunch, for_funestra
from fun_time.window_layout import WindowRect
from satellite.contract import SatelliteChannels


def _launch(funestra: FunTimeFunestra, tmp_path: Path) -> SatelliteLaunch:
    return SatelliteLaunch(
        funestra=funestra,
        sources=str(tmp_path / funestra.label),
        channels=SatelliteChannels(
            playlist=tmp_path / f"{funestra.label}_playlist.tsv",
            command=tmp_path / f"{funestra.label}_cmd.txt",
            paused=tmp_path / f"{funestra.label}_paused.txt",
            status=tmp_path / f"{funestra.label}_status.txt",
            play_points=Path("points.json")),
        log_file=tmp_path / f"{funestra.label}.log",
        rect=WindowRect(x=0, y=0, width=100, height=100),
    )


def test_for_satellite_hands_back_the_matching_slot(tmp_path: Path):
    launch = _launch(FunTimeFunestra.PORTRAIT, tmp_path)
    assert for_funestra(launch, FunTimeFunestra.PORTRAIT) is launch


def test_for_satellite_refuses_the_swap(tmp_path: Path):
    with pytest.raises(ValueError, match="portrait launch was handed to landscape"):
        for_funestra(_launch(FunTimeFunestra.PORTRAIT, tmp_path), FunTimeFunestra.LANDSCAPE)
