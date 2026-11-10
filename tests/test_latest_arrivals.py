from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from funestra.funestra_verbs import play_next
from funestra.modes import MainMode
from funestra.playlist import PlaylistItem

from fun_time.latest_arrivals import LatestArrivals
from fun_time.media_metadata import metadata_path_for
from fun_time.shared_state import BridgeState
from tests.test_windows_bridge_dispatch_loop import make_config

MAIN_LATEST = replace(BridgeState(), main_latest=True)


def _video(folder: Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"\x00")
    return str(path)


def _watching(tmp_path, **config) -> tuple[LatestArrivals, object]:
    config = make_config(tmp_path, **config)
    if config.main_sources:
        _video(Path(config.main_sources), "already there.mp4")
    arrivals = LatestArrivals(config)
    arrivals.look()
    return arrivals, config


def _queued(cmd_file: Path) -> list[str]:
    return cmd_file.read_text(encoding="utf-8").splitlines() if cmd_file.exists() else []


def _next(path: str, funscript: Path | None = None) -> str:
    return play_next(PlaylistItem(Path(path), funscript))


class TestTheMainFunestraPlayingItsLatestList:
    def test_puts_what_arrives_in_its_library_up_next(self, tmp_path):
        library = tmp_path / "videos"
        arrivals, config = _watching(tmp_path, main_sources=str(library))
        newcomer = _video(library, "feature two.mp4")
        arrivals.look()

        arrivals.deliver(MAIN_LATEST)

        assert _queued(config.main_funestra_cmd_file) == [_next(newcomer)]

    def test_holds_what_arrives_while_genau_has_the_main_funestra(self, tmp_path):
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
