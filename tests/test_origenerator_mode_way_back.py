from __future__ import annotations

from pathlib import Path

from fun_time.fun_time_funestras import FunTimeFunestra
from tests.integration.test_origenerator_mode_integration import _bring_the_satellites_home

_POLLS_AN_APP_ON_A_BUSY_MACHINE_TAKES_TO_ANSWER = 3


class _Satellite:
    def __init__(self, app: _AnAppOnlyPollsMoveOn, funestra: FunTimeFunestra,
                 own_video: Path, image: Path):
        self._own_video = own_video
        self._image = image
        self.playlist_file = app.state_dir / f"{funestra.label}_playlist.tsv"
        self.status_file = app.state_dir / f"{funestra.label}_status.txt"
        self.taken = False
        self.handed_back = False
        self.polls = 0
        self._play(own_video)

    def _play(self, video: Path) -> None:
        self.playlist_file.write_text(f"{video}\n", encoding="utf-8")
        self.status_file.write_text(f"video={video}\n", encoding="utf-8")

    def plays_its_own_video(self) -> bool:
        return self.status_file.read_text(encoding="utf-8").strip() == f"video={self._own_video}"

    def polled(self) -> None:
        self.polls += 1
        if self.handed_back:
            return
        if self.polls >= _POLLS_AN_APP_ON_A_BUSY_MACHINE_TAKES_TO_ANSWER:
            self.taken = True
            self._play(self._image)

    def way_back_pressed(self) -> None:
        if self.taken:
            self.taken = False
            self.handed_back = True
            self._play(self._own_video)


class _Paths:
    def __init__(self, portrait_dir: Path, landscape_dir: Path):
        self.portrait_dirs = [portrait_dir]
        self.landscape_dirs = [landscape_dir]


class _AnAppOnlyPollsMoveOn:
    def __init__(self, tmp_path: Path):
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        images = tmp_path / "images"
        images.mkdir()
        folders = {}
        self._satellites = {}
        self.presses: list[str] = []
        self.taken_when_pressed: list[bool] = []
        for funestra in FunTimeFunestra.SATELLITES:
            folder = tmp_path / funestra.label
            folder.mkdir()
            folders[funestra] = folder
            self._satellites[funestra] = _Satellite(
                self, funestra, folder / f"{funestra.label}-clip-one.mp4",
                images / f"{funestra.label}.png")
        self.config = self
        self.paths = _Paths(folders[FunTimeFunestra.PORTRAIT], folders[FunTimeFunestra.LANDSCAPE])

    def satellite(self, funestra: FunTimeFunestra) -> _Satellite:
        satellite = self._satellites[funestra]
        satellite.polled()
        return satellite

    def write_dashboard_command(self, command: str) -> None:
        self.presses.append(command)
        pressed = next(satellite for funestra, satellite in self._satellites.items()
                       if command == f"{funestra.label}_kino_activate")
        self.taken_when_pressed.append(pressed.taken)
        pressed.way_back_pressed()

    def polled_as_the_next_test_would(self) -> list[_Satellite]:
        for _poll in range(_POLLS_AN_APP_ON_A_BUSY_MACHINE_TAKES_TO_ANSWER):
            satellites = [self.satellite(funestra) for funestra in FunTimeFunestra.SATELLITES]
        return satellites


def test_the_way_back_is_not_pressed_until_the_app_has_both_funestras(tmp_path):
    app = _AnAppOnlyPollsMoveOn(tmp_path)

    _bring_the_satellites_home(app)

    assert app.presses == ["portrait_kino_activate", "landscape_kino_activate"]
    assert app.taken_when_pressed == [True, True]


def test_none_of_the_apps_lists_land_in_the_test_after_the_way_back(tmp_path):
    app = _AnAppOnlyPollsMoveOn(tmp_path)

    _bring_the_satellites_home(app)

    assert all(satellite.plays_its_own_video()
               for satellite in app.polled_as_the_next_test_would())
