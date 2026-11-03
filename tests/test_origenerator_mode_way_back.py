from __future__ import annotations

from pathlib import Path

from fun_time.fun_time_funestras import FunTimeFunestra
from tests.integration.test_origenerator_mode_integration import _bring_the_satellites_home

_LOOKS_BEFORE_THE_APP_ANSWERS = 2


class _Satellite:
    def __init__(self, app: _App, funestra: FunTimeFunestra, own_video: Path, image: Path):
        self._app = app
        self._funestra = funestra
        self._own_video = own_video
        self._image = image
        self.playlist_file = app.state_dir / f"{funestra.label}_playlist.tsv"
        self.status_file = app.state_dir / f"{funestra.label}_status.txt"
        self.taken = False
        self.looks = 0
        self._play(own_video)

    def _play(self, video: Path) -> None:
        self.playlist_file.write_text(f"{video}\n", encoding="utf-8")
        self.status_file.write_text(f"video={video}\n", encoding="utf-8")

    def looked_at(self) -> None:
        self.looks += 1
        if self._app.pressed:
            self.taken = False
            self._play(self._own_video)
        elif self.looks >= _LOOKS_BEFORE_THE_APP_ANSWERS:
            self.taken = True
            self._play(self._image)


class _Paths:
    def __init__(self, portrait_dir: Path, landscape_dir: Path):
        self.portrait_dirs = [portrait_dir]
        self.landscape_dirs = [landscape_dir]


class _App:
    def __init__(self, tmp_path: Path):
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        images = tmp_path / "images"
        images.mkdir()
        folders = {}
        self._satellites = {}
        self.pressed = False
        self.presses: list[str] = []
        self.taken_when_pressed: bool | None = None
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
        satellite.looked_at()
        return satellite

    def write_dashboard_command(self, command: str) -> None:
        self.presses.append(command)
        self.taken_when_pressed = all(
            satellite.taken for satellite in self._satellites.values())
        self.pressed = True


def test_the_way_back_is_not_pressed_until_the_app_has_both_funestras(tmp_path):
    app = _App(tmp_path)

    _bring_the_satellites_home(app)

    assert app.presses == ["portrait_kino_activate", "landscape_kino_activate"]
    assert app.taken_when_pressed
