"""How the integration suite presses its way back out of the hosted app's mode.

The wait that follows the press — each player playing its own clips again —
cannot tell a player handed its list back from one the app never took.  So the
press has to come after the app has both players, or it is answered by a room
that never entered the mode at all, and the app's lists land on the players
after the test that pressed it has ended.  The test that runs next then reads a
playlist holding the app's picture and reports that the players had no lists of
their own, which is how the hosted-shows test went red on 2026-09-16 and again
on 2026-09-18.
"""
from __future__ import annotations

from pathlib import Path

from fun_time.players import Player
from tests.integration.test_origenerator_mode_integration import _leave_the_mode

# How many looks at a side the hosted app takes to answer the OPEN_SHOWS the
# switch sent it — a couple, as it does on a machine with other work on it.
_LOOKS_BEFORE_THE_APP_ANSWERS = 2


class _Side:
    """One player's playlist and status files, as the session addresses them."""

    def __init__(self, room: _Room, player: Player, own_clip: Path, picture: Path):
        self._room = room
        self._player = player
        self._own_clip = own_clip
        self._picture = picture
        self.playlist_file = room.state_dir / f"{player.label}_playlist.tsv"
        self.status_file = room.state_dir / f"{player.label}_status.txt"
        self.taken = False
        self.looks = 0
        self._play(own_clip)

    def _play(self, clip: Path) -> None:
        self.playlist_file.write_text(f"{clip}\n", encoding="utf-8")
        self.status_file.write_text(f"video={clip}\n", encoding="utf-8")

    def looked_at(self) -> None:
        self.looks += 1
        if self._room.pressed:
            self.taken = False
            self._play(self._own_clip)
        elif self.looks >= _LOOKS_BEFORE_THE_APP_ANSWERS:
            self.taken = True
            self._play(self._picture)


class _Paths:
    def __init__(self, portrait_dir: Path, landscape_dir: Path):
        self.portrait_dirs = [portrait_dir]
        self.landscape_dirs = [landscape_dir]


class _Room:
    """A session whose hosted app takes a couple of looks to take the players.

    Addressing a side — which every poll of that side does — is what moves the
    room on, so the room runs on the suite's own polling rather than on a clock
    another agent's machine would keep differently.
    """

    def __init__(self, tmp_path: Path):
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        pictures = tmp_path / "pictures"
        pictures.mkdir()
        folders = {}
        self._sides = {}
        self.pressed = False
        self.presses: list[str] = []
        self.taken_when_pressed: bool | None = None
        for player in Player.SATELLITES:
            folder = tmp_path / player.label
            folder.mkdir()
            folders[player] = folder
            self._sides[player] = _Side(
                self, player, folder / f"{player.label}-clip-one.mp4",
                pictures / f"{player.label}.png")
        self.config = self
        self.paths = _Paths(folders[Player.PORTRAIT], folders[Player.LANDSCAPE])

    def side(self, player: Player) -> _Side:
        side = self._sides[player]
        side.looked_at()
        return side

    def write_dashboard_command(self, command: str) -> None:
        self.presses.append(command)
        self.taken_when_pressed = all(
            side.taken for side in self._sides.values())
        self.pressed = True


def test_the_way_back_is_not_pressed_until_the_app_has_both_players(tmp_path):
    room = _Room(tmp_path)

    _leave_the_mode(room)

    assert room.presses == ["satellites_video_activate"]
    assert room.taken_when_pressed
