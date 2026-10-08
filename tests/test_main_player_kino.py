"""Kino as it runs on the Main Funestra: its verbs, its pass each frame, its lines
in the status file and the console's top block, over a real Playback."""
from __future__ import annotations

import json
from pathlib import Path

from player_core.modes import LengthMode, LoopState
from player_core.playback import Playback
from player_core.playlist import PlaylistItem

from main_player.clip_nav import ClipNav
from main_player.kino import Kino
from main_player.library import LibraryEntry
from main_player.mode_memory import ModeMemory, RememberedMode
from tests.satellite_fakes import FakeSatellitePlayer


class FakeSource:
    def __init__(self, entries: list[LibraryEntry], version_index=None, metadata_root=None) -> None:
        self.entries = entries
        self.genau_clips: list[LibraryEntry] = []
        self.version_index = version_index or {}
        self.metadata_root = metadata_root
        self.asked: list[str] = []

    def playlist_for(self, mode: str) -> list[PlaylistItem]:
        self.asked.append(mode)
        return [PlaylistItem(entry.video, entry.funscript) for entry in reversed(self.entries)]


class FakeNotices:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def say(self, message: str, *, level) -> None:
        self.said.append((message, level))


def _clip(lib: Path, meta: Path, rel: str, comp: str, index: int) -> Path:
    video = lib / rel
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")
    sidecar = (meta / video.relative_to(lib)).with_suffix(".json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({
        "clip": {"compilation": comp, "index": index, "source": f"scene {index}", "performer": "Jane Doe"},
    }), encoding="utf-8")
    return video


class World:
    def __init__(self, tmp_path: Path, *, remembered: RememberedMode | None = None,
                 scripted: bool = True) -> None:
        lib, meta = tmp_path / "videos", tmp_path / "metadata"
        self.first = _clip(lib, meta, "w/Jane Doe - Scene One.mp4", "Vol6", 1)
        self.second = _clip(lib, meta, "w/Ann Bly - Scene Two.mp4", "Vol6", 2)
        self.scripts = {}
        if scripted:
            for video in (self.first, self.second):
                script = video.with_suffix(".funscript")
                script.write_text('{"actions": [{"at": 0, "pos": 100}, {"at": 1000, "pos": 0}, '
                                  '{"at": 2000, "pos": 100}, {"at": 3000, "pos": 0}, {"at": 4000, "pos": 100}]}',
                                  encoding="utf-8")
                self.scripts[video] = script
        entries = [LibraryEntry(video, self.scripts.get(video), 1) for video in (self.first, self.second)]
        self.source = FakeSource(entries, metadata_root=meta)
        self.player = FakeSatellitePlayer(duration_ms=60_000.0)
        self.playback = Playback([self.first, self.second], player=self.player, locked=True,
                                 funscripts=dict(self.scripts))
        self.memory = ModeMemory(tmp_path / "main_player_mode.txt")
        self.notices = FakeNotices()
        self.resolved: list[list[PlaylistItem]] = [[PlaylistItem(self.second), PlaylistItem(self.first)]]
        self.kino = Kino(
            self.playback, source=self.source, clip_nav=ClipNav.build([self.first, self.second], meta),
            notices=self.notices, memory=self.memory, remembered=remembered or RememberedMode(),
            resolve_playlist=lambda: self.resolved[-1])


class TestItsVerbs:
    def test_a_loop_gesture_marks_the_playback_where_the_playhead_is(self, tmp_path):
        world = World(tmp_path)
        world.player.position_ms = 2_500.0

        assert world.kino.apply_command("RECORD_TAP") is True

        assert world.playback.mark == 2_500
        assert world.kino.status_fields()["loop_state"] == str(LoopState.RECORDING)

    def test_a_second_tap_hands_the_playback_the_range_snapped_to_the_script(self, tmp_path):
        world = World(tmp_path)
        world.player.position_ms = 2_500.0
        world.kino.apply_command("RECORD_TAP")
        world.player.position_ms = 3_500.0

        world.kino.apply_command("RECORD_TAP")

        assert world.playback.ab_loop == (2_000, 4_000)
        assert world.player.ab_loop == (2_000, 4_000)
        fields = world.kino.status_fields()
        assert (fields["loop_state"], fields["loop_in_ms"], fields["loop_out_ms"]) == (
            str(LoopState.LOOPING), "2000", "4000")

    def test_a_funestras_own_verb_is_refused_so_the_funestra_answers_it(self, tmp_path):
        world = World(tmp_path)

        assert world.kino.apply_command("NEXT") is False
        assert world.playback.index == 0

    def test_the_other_version_is_the_librarys_to_name(self, tmp_path):
        world = World(tmp_path)
        upscale = world.first.with_name("Jane Doe - Scene One_topaz.mp4")
        upscale.write_bytes(b"x")
        family = [PlaylistItem(world.first), PlaylistItem(upscale)]
        world.source.version_index = {world.first: family, upscale: family}
        world = World(tmp_path)
        world.source.version_index = {world.first: family, upscale: family}
        kino = Kino(world.playback, source=world.source, clip_nav=ClipNav.build([world.first], None),
                    notices=world.notices, memory=world.memory, remembered=RememberedMode(),
                    resolve_playlist=list)

        assert kino.apply_command("CYCLE_VERSION") is True

        assert world.playback.showing == upscale
        assert kino.status_fields()["has_other_versions"] == "1"

    def test_reload_playlist_reads_the_list_through_the_library(self, tmp_path):
        world = World(tmp_path)

        assert world.kino.apply_command("RELOAD_PLAYLIST") is True

        assert world.playback.playlist == [world.second, world.first]
        assert world.playback.current_video == world.first

    def test_a_length_named_rebuilds_the_list_and_is_written_down(self, tmp_path):
        world = World(tmp_path)

        assert world.kino.apply_command("SET_LENGTH_MODE shorts") is True
        world.kino.tick()

        assert world.source.asked == [LengthMode.SHORTS]
        assert world.playback.current_video == world.second
        assert world.memory.read().length_mode == LengthMode.SHORTS


class TestItsPassEachFrame:
    def test_a_mark_that_reaches_the_end_of_the_item_closes_into_a_running_loop(self, tmp_path):
        world = World(tmp_path, scripted=False)
        world.player.position_ms = 59_000.0
        world.kino.apply_command("RECORD_DOWN")
        world.player.position_ms = 59_950.0

        world.kino.tick()

        assert world.playback.ab_loop == (59_000, 59_950)
        assert world.player.seeks[-1] == 59_000

    def test_a_paused_playback_is_left_marking(self, tmp_path):
        world = World(tmp_path, scripted=False)
        world.player.position_ms = 59_000.0
        world.kino.apply_command("RECORD_DOWN")
        world.playback.set_paused(True)
        world.player.position_ms = 59_950.0

        world.kino.tick()

        assert world.playback.ab_loop is None
        assert world.playback.mark == 59_000

    def test_an_item_opened_on_the_playback_starts_with_no_loop(self, tmp_path):
        world = World(tmp_path)
        world.kino.apply_command("SET_LOOP 2000 4000")

        world.playback.step(1)
        world.kino.tick()

        assert world.kino.status_fields()["loop_state"] == str(LoopState.NORMAL)
        assert world.playback.ab_loop is None

    def test_the_same_item_opened_again_starts_with_no_loop_too(self, tmp_path):
        world = World(tmp_path)
        world.kino.apply_command("SET_LOOP 2000 4000")

        world.playback.play_file(world.first)

        assert world.kino.status_fields()["loop_state"] == str(LoopState.NORMAL)


class TestWhatItSays:
    def test_the_console_leads_with_the_librarys_title_and_the_place_in_the_list(self, tmp_path):
        world = World(tmp_path)

        hud = world.kino.top_block()

        assert hud.video == "Jane Doe - scene 1"
        assert (hud.position, hud.total) == (1, 2)
        assert hud.length_mode == LengthMode.MIXED

    def test_the_status_carries_the_videos_place_in_the_library(self, tmp_path):
        world = World(tmp_path)

        fields = world.kino.status_fields()

        assert fields["length_mode"] == "mixed"
        assert fields["has_compilation"] == "1"
        assert fields["compilation"] == ""

    def test_a_remembered_compilation_is_re_entered_around_its_clip(self, tmp_path):
        world = World(tmp_path, remembered=RememberedMode(
            length_mode=LengthMode.MIXED, compilation="Vol6", video=str(World(tmp_path).second)))

        assert world.kino.status_fields()["compilation"] == "Vol6"
        assert world.playback.current_video == world.second
