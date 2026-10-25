from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from funestra.funestra_verbs import play_next
from funestra.modes import MainMode
from funestra.playlist import PlaylistItem

from fun_time.latest_arrivals import LatestArrivals
from fun_time.lock_hud import prime_group_indexes
from fun_time.media_metadata import cached_group_index, mark_favorite, metadata_path_for
from fun_time.origenerator_switches import ORIGENERATOR_MODE
from fun_time.room_funestras import RoomFunestra
from fun_time.shared_state import BridgeState
from tests.test_windows_bridge_dispatch_loop import make_config

PORTRAIT_LATEST = BridgeState().with_satellite(RoomFunestra.PORTRAIT, latest=True)
MAIN_LATEST = replace(BridgeState(), main_latest=True)


def _video(folder: Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"\x00")
    return str(path)


def _watching(tmp_path, **config) -> tuple[LatestArrivals, object]:
    config = make_config(tmp_path, **config)
    for sources in (config.main_sources, config.portrait_sources, config.landscape_sources):
        if sources:
            _video(Path(sources), "already there.mp4")
    arrivals = LatestArrivals(config)
    arrivals.look()
    return arrivals, config


def _queued(cmd_file: Path) -> list[str]:
    return cmd_file.read_text(encoding="utf-8").splitlines() if cmd_file.exists() else []


def _next(path: str, funscript: Path | None = None) -> str:
    return play_next(PlaylistItem(Path(path), funscript))


class TestASatellitePlayingItsLatestList:
    def test_puts_what_arrives_in_its_library_up_next(self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library))
        newcomer = _video(library, "scene two.mp4")
        arrivals.look()

        arrivals.deliver(PORTRAIT_LATEST)

        assert _queued(config.portrait_cmd_file) == [_next(newcomer)]

    def test_its_hud_knows_the_groups_of_what_it_put_up_without_a_rescan(self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library))
        prime_group_indexes((config.portrait_sources,), config.regen_metadata_root)
        newcomer = _video(library, "scene two.mp4")
        arrivals.look()

        arrivals.deliver(PORTRAIT_LATEST)

        assert cached_group_index(config.portrait_sources, paths_supplier=list,
                                  metadata_root=None).contains(newcomer)

    def test_in_f_mode_puts_up_only_the_favorites_among_what_arrives(self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library))
        favorite, _other = _video(library, "alpha.mp4"), _video(library, "beta.mp4")
        config.regen_metadata_root = tmp_path / "metadata"
        mark_favorite(favorite, config.regen_metadata_root, True)
        arrivals.look()

        arrivals.deliver(PORTRAIT_LATEST.with_satellite(
            RoomFunestra.PORTRAIT, favorites_filter=True))

        assert _queued(config.portrait_cmd_file) == [_next(favorite)]

    def test_hands_several_over_oldest_first_so_the_newest_comes_up_first(self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library))
        newer, older = _video(library, "alpha.mp4"), _video(library, "beta.mp4")
        os.utime(older, (1_000_000_000, 1_000_000_000))
        arrivals.look()

        arrivals.deliver(PORTRAIT_LATEST)

        assert _queued(config.portrait_cmd_file) == [_next(older), _next(newer)]

    def test_holds_what_arrives_through_a_loop_and_puts_it_up_next_when_the_loop_ends(
            self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library))
        newcomer = _video(library, "scene two.mp4")
        arrivals.look()

        arrivals.deliver(PORTRAIT_LATEST.with_satellite(RoomFunestra.PORTRAIT, loop="seed"))
        held = _queued(config.portrait_cmd_file)
        arrivals.deliver(PORTRAIT_LATEST)

        assert held == []
        assert _queued(config.portrait_cmd_file) == [_next(newcomer)]

    def test_holds_what_arrives_while_it_plays_origenerators_slideshows(self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library),
                                     origenerator_enabled=True,
                                     origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")
        newcomer = _video(library, "scene two.mp4")
        arrivals.look()

        arrivals.deliver(PORTRAIT_LATEST.with_satellite(
            RoomFunestra.PORTRAIT, mode=ORIGENERATOR_MODE))
        held = _queued(config.portrait_cmd_file)
        arrivals.deliver(PORTRAIT_LATEST)

        assert held == []
        assert _queued(config.portrait_cmd_file) == [_next(newcomer)]

    def test_holds_what_arrives_until_it_is_back_on_its_own_list(self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library))
        newcomer = _video(library, "scene two.mp4")
        arrivals.look()

        arrivals.deliver(PORTRAIT_LATEST, coming_home={RoomFunestra.PORTRAIT})
        held = _queued(config.portrait_cmd_file)
        arrivals.deliver(PORTRAIT_LATEST)

        assert held == []
        assert _queued(config.portrait_cmd_file) == [_next(newcomer)]


class TestASatelliteShuffling:
    def test_is_handed_nothing_and_nothing_that_came_meanwhile_waits_for_its_latest(self, tmp_path):
        library = tmp_path / "portrait"
        arrivals, config = _watching(tmp_path, portrait_sources=str(library))
        _video(library, "scene two.mp4")
        arrivals.look()

        arrivals.deliver(BridgeState())
        arrivals.deliver(PORTRAIT_LATEST)

        assert _queued(config.portrait_cmd_file) == []


class TestTheMainFunestraPlayingItsLatestList:
    def test_puts_what_arrives_in_its_library_up_next(self, tmp_path):
        library = tmp_path / "videos"
        arrivals, config = _watching(tmp_path, main_sources=str(library))
        newcomer = _video(library, "feature two.mp4")
        arrivals.look()

        arrivals.deliver(MAIN_LATEST)

        assert _queued(config.main_funestra_cmd_file) == [_next(newcomer)]

    def test_holds_what_arrives_while_genau_has_the_main_slot(self, tmp_path):
        library = tmp_path / "videos"
        arrivals, config = _watching(tmp_path, main_sources=str(library))
        newcomer = _video(library, "feature two.mp4")
        arrivals.look()

        arrivals.deliver(replace(MAIN_LATEST, main_mode=MainMode.GENAU))
        held = _queued(config.main_funestra_cmd_file)
        arrivals.deliver(MAIN_LATEST)

        assert held == []
        assert _queued(config.main_funestra_cmd_file) == [_next(newcomer)]

    def test_holds_what_arrives_while_a_compilation_plays(self, tmp_path):
        library = tmp_path / "videos"
        arrivals, config = _watching(tmp_path, main_sources=str(library))
        newcomer = _video(library, "feature two.mp4")
        arrivals.look()
        config.main_funestra_status_file.write_text("compilation=Anthology One\n", encoding="utf-8")

        arrivals.deliver(MAIN_LATEST)
        held = _queued(config.main_funestra_cmd_file)
        config.main_funestra_status_file.write_text("compilation=\n", encoding="utf-8")
        arrivals.deliver(MAIN_LATEST)

        assert held == []
        assert _queued(config.main_funestra_cmd_file) == [_next(newcomer)]

    def test_in_f_mode_puts_up_only_a_video_someone_wrote_a_funscript_for(self, tmp_path):
        library = tmp_path / "library"
        videos, scripts = library / "videos" / "videos", library / "videos" / "scripts" / "scripts"
        arrivals, config = _watching(tmp_path, main_sources=str(videos),
                                     regen_metadata_root=library / "videos" / "metadata")
        scripted, _unscripted = _video(videos, "feature two.mp4"), _video(videos, "feature three.mp4")
        script = Path(_video(scripts, "feature two.funscript"))
        arrivals.look()

        arrivals.deliver(replace(MAIN_LATEST, main_scripted_filter=True))

        assert _queued(config.main_funestra_cmd_file) == [_next(scripted, script)]

    def test_with_landscape_left_out_puts_up_only_a_portrait_video(self, tmp_path):
        library = tmp_path / "library"
        videos, metadata = library / "videos" / "videos", library / "videos" / "metadata"
        arrivals, config = _watching(tmp_path, main_sources=str(videos), regen_metadata_root=metadata)
        portrait, _landscape = _video(videos, "feature two.mp4"), _video(videos, "feature three.mp4")
        sidecar = metadata_path_for(Path(portrait), metadata)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"video": {"orientation": "portrait"}}), encoding="utf-8")
        arrivals.look()

        arrivals.deliver(replace(MAIN_LATEST, main_plays_landscape=False))

        assert _queued(config.main_funestra_cmd_file) == [_next(portrait)]
