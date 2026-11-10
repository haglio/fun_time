from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from funestra.main_hud import OSR2_DRIVING
from funestra.modes import MainMode

from fun_time.config import SatelliteChannel
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.main_list import MainList, MainListSettings
from fun_time.main_list_builds import BUILDS_HERE, MainListBuild
from fun_time.media_metadata import metadata_path_for
from fun_time.mode_plan import EnhancementLevel
from fun_time.modes import SatelliteBuild
from fun_time.omnipause import PausedFiles, apply_enter_omnipause, apply_leave_omnipause
from fun_time.runtime_flow import (
    FMODE_FUNESTRAS,
    SatelliteFmodeInputs,
    apply_fmode,
    apply_mode_switch,
    apply_satellite_filter,
    satellite_browse_paths,
)
from tests.made_up_genau import kino_upscales_at


def _make_action_video(
    folder: Path, metadata_root: Path, name: str, action: str, prompt: str = "x"
) -> str:
    video = folder / f"{name}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")

    sidecar = metadata_path_for(video, metadata_root)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps({"video": {"action": action, "labels": [action], "prompt": prompt, "seed": name}}), encoding="utf-8"
    )
    return str(video)


def _channel(tmp_path: Path, label: str, sources, cmd_file: Path) -> SatelliteChannel:
    channel = SatelliteChannel.in_state_dir(tmp_path, FunTimeFunestra[label.upper()],
                                            sources=str(sources))
    return replace(channel, cmd_file=cmd_file,
                   playlist_file=tmp_path / "state" / f"{label}_playlist.tsv")


def _satellite_lines(state_dir: Path, funestra: str) -> list[str]:
    return (state_dir / f"{funestra}_playlist.tsv").read_text(encoding="utf-8").splitlines()


def _reloaded(cmd_file: Path) -> bool:
    if not cmd_file.exists():
        return False
    return "RELOAD_PLAYLIST" in cmd_file.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def flow_files(tmp_path: Path) -> dict[str, Path]:
    return {
        "portrait_paused_file": tmp_path / "portrait_paused.txt",
        "landscape_paused_file": tmp_path / "landscape_paused.txt",
        "genau_paused_file": tmp_path / "genau_paused.txt",
        "audio_paused_file": tmp_path / "audio_paused.txt",
        "genau_cmd_file": tmp_path / "genau_cmd.txt",
        "main_funestra_paused_file": tmp_path / "main_player_paused.txt",
        "main_funestra_cmd_file": tmp_path / "main_player_cmd.txt",
        "broker_cmd_file": tmp_path / "broker_cmd.txt",
    }


def _paused(files) -> PausedFiles:
    return PausedFiles(
        portrait=files["portrait_paused_file"], landscape=files["landscape_paused_file"],
        genau=files["genau_paused_file"], audio=files["audio_paused_file"],
        main_funestra=files["main_funestra_paused_file"],
    )


def _mode_switch(files, *, current, target, omni_paused=False):
    return apply_mode_switch(
        current_mode=current,
        target_mode=target,
        omni_paused=omni_paused,
        main_funestra_paused_file=files["main_funestra_paused_file"],
        main_funestra_cmd_file=files["main_funestra_cmd_file"],
    )


def _main_funestra_cmds(files) -> list[str]:
    cmd_file = files["main_funestra_cmd_file"]
    return cmd_file.read_text(encoding="utf-8").split("\n")[:-1] if cmd_file.exists() else []


def test_kino_to_genau_hands_genau_the_window_and_holds_the_video(flow_files):
    result = _mode_switch(flow_files, current="kino", target="genau")

    assert result.next_mode == "genau"
    assert result.is_transition is True
    assert flow_files["main_funestra_paused_file"].read_text(encoding="utf-8") == "1"
    assert _main_funestra_cmds(flow_files) == ["SHOW genau"]


def test_genau_to_kino_starts_the_video_and_hands_kino_the_window(flow_files):
    result = _mode_switch(flow_files, current="genau", target="kino")

    assert result.next_mode == "kino"
    assert flow_files["main_funestra_paused_file"].read_text(encoding="utf-8") == "0"
    assert _main_funestra_cmds(flow_files) == ["SHOW kino"]


def test_a_mode_switch_leaves_who_drives_the_osr2_to_the_arbiter(flow_files):
    for current, target in (("kino", "genau"), ("genau", "kino")):
        _mode_switch(flow_files, current=current, target=target)

    assert not flow_files["genau_paused_file"].exists()
    assert not flow_files["audio_paused_file"].exists()
    assert not flow_files["genau_cmd_file"].exists()
    assert not any(verb.startswith("SET_TCODE_ENABLED") for verb in _main_funestra_cmds(flow_files))


def test_a_mode_switch_during_omnipause_hands_over_the_window_and_resumes_nothing(flow_files):
    result = _mode_switch(flow_files, current="kino", target="genau", omni_paused=True)

    assert result.next_mode == "genau"
    assert not flow_files["genau_cmd_file"].exists()
    assert _main_funestra_cmds(flow_files) == ["SHOW genau"]
    assert not flow_files["main_funestra_paused_file"].exists()


def test_a_mode_switch_queues_its_verb_behind_what_the_file_already_holds(flow_files):
    flow_files["main_funestra_cmd_file"].write_text("SET_VOLUME 1\n", encoding="utf-8")

    _mode_switch(flow_files, current="kino", target="genau")

    assert _main_funestra_cmds(flow_files) == ["SET_VOLUME 1", "SHOW genau"]


def test_toggle_fmode_replaces_playlists_and_reloads_main_funestra(tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    portrait_root = tmp_path / "portrait"
    landscape_root = tmp_path / "landscape"
    for root in (primary_root, portrait_root, landscape_root):
        root.mkdir(parents=True)
    main_video = primary_root / "main.mp4"
    portrait_video = portrait_root / "portrait.mp4"
    landscape_video = landscape_root / "landscape.mp4"
    for path in (main_video, portrait_video, landscape_video):
        path.write_text("x", encoding="utf-8")
    mirrored = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "main.funscript"
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    mirrored.write_text("{}", encoding="utf-8")
    state_dir = tmp_path / "state"
    portrait_cmd_file = tmp_path / "portrait_cmd.txt"
    landscape_cmd_file = tmp_path / "landscape_cmd.txt"
    main_funestra_cmd_file = tmp_path / "main_player_cmd.txt"

    result = apply_fmode(
        satellites={
            FunTimeFunestra.PORTRAIT: SatelliteFmodeInputs(_channel(tmp_path, "portrait", str(portrait_root), portrait_cmd_file), recent=False),
            FunTimeFunestra.LANDSCAPE: SatelliteFmodeInputs(_channel(tmp_path, "landscape", str(landscape_root), landscape_cmd_file), recent=False),
        },
        funestras=FMODE_FUNESTRAS,
        enabled=True,
        main_build=MainListBuild(main_list=MainList(sources=str(primary_root), state_dir=tmp_path / "state"), settings=MainListSettings(), main_funestra_cmd_file=main_funestra_cmd_file),
    )

    assert result.funestras == FMODE_FUNESTRAS
    assert result.enabled is True
    assert portrait_cmd_file.read_text(encoding="utf-8").splitlines() == ["RELOAD_PLAYLIST"]
    assert landscape_cmd_file.read_text(encoding="utf-8").splitlines() == ["RELOAD_PLAYLIST"]
    assert "RELOAD_PLAYLIST" in main_funestra_cmd_file.read_text(encoding="utf-8").splitlines()
    assert (state_dir / "portrait_playlist.tsv").exists()
    assert (state_dir / "landscape_playlist.tsv").exists()
    assert (state_dir / "main_player_playlist.tsv").exists()


def test_fmode_on_one_funestra_leaves_the_others_playlists_untouched(tmp_path: Path):
    portrait_root, landscape_root = tmp_path / "portrait", tmp_path / "landscape"
    for root in (portrait_root, landscape_root):
        root.mkdir(parents=True)
        (root / "clip.mp4").write_text("x", encoding="utf-8")
    state_dir = tmp_path / "state"
    landscape_cmd_file = tmp_path / "landscape_cmd.txt"
    main_funestra_cmd_file = tmp_path / "main_player_cmd.txt"

    result = apply_fmode(
        satellites={
            FunTimeFunestra.PORTRAIT: SatelliteFmodeInputs(_channel(tmp_path, "portrait", str(portrait_root), tmp_path / "portrait_cmd.txt"), recent=False),
            FunTimeFunestra.LANDSCAPE: SatelliteFmodeInputs(_channel(tmp_path, "landscape", str(landscape_root), landscape_cmd_file), recent=False),
        },
        funestras=(FunTimeFunestra.PORTRAIT,),
        enabled=True,
        main_build=MainListBuild(main_list=MainList(sources="", state_dir=tmp_path / "state"), settings=MainListSettings(), main_funestra_cmd_file=main_funestra_cmd_file),
    )

    assert result.funestras == (FunTimeFunestra.PORTRAIT,)
    assert (state_dir / "portrait_playlist.tsv").exists()
    assert not (state_dir / "landscape_playlist.tsv").exists()
    assert not (state_dir / "main_player_playlist.tsv").exists()
    assert not landscape_cmd_file.exists()
    assert not main_funestra_cmd_file.exists()


def test_f_mode_is_told_right_behind_the_reload(tmp_path: Path):
    root = tmp_path / "videos" / "videos" / "primary"
    root.mkdir(parents=True)
    (root / "main.mp4").write_text("x", encoding="utf-8")
    main_funestra_cmd_file = tmp_path / "main_player_cmd.txt"

    def told(enabled: bool) -> list[str]:
        main_funestra_cmd_file.unlink(missing_ok=True)
        apply_fmode(
            satellites={
                FunTimeFunestra.PORTRAIT: SatelliteFmodeInputs(_channel(tmp_path, "portrait", "", tmp_path / "p_cmd.txt")),
                FunTimeFunestra.LANDSCAPE: SatelliteFmodeInputs(_channel(tmp_path, "landscape", "", tmp_path / "l_cmd.txt")),
            },
            funestras=FMODE_FUNESTRAS,
            enabled=enabled,
            main_build=MainListBuild(main_list=MainList(sources=str(root), state_dir=tmp_path / "state"), settings=MainListSettings(), main_funestra_cmd_file=main_funestra_cmd_file),
        )
        return main_funestra_cmd_file.read_text(encoding="utf-8").splitlines()

    assert told(True)[:2] == ["RELOAD_PLAYLIST", "SET_F_MODE 1"]
    assert told(False) == ["RELOAD_PLAYLIST", "SET_F_MODE 0"]


def _the_verbs_of_a_build_started_at_the_top(tmp_path: Path) -> tuple[list[str], Path, Path]:
    root = tmp_path / "videos" / "videos" / "primary"
    root.mkdir(parents=True)
    old, new = root / "old.mp4", root / "new.mp4"
    for path, mtime in ((old, 1000), (new, 2000)):
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))
    script = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "new.funscript"
    script.parent.mkdir(parents=True)
    script.write_text("{}", encoding="utf-8")
    main_funestra_cmd_file = tmp_path / "main_player_cmd.txt"

    BUILDS_HERE.build(MainListBuild(
        main_list=MainList(sources=str(root), state_dir=tmp_path / "state"),
        settings=MainListSettings(latest=True),
        main_funestra_cmd_file=main_funestra_cmd_file, start_at_top=True,
    ))

    return main_funestra_cmd_file.read_text(encoding="utf-8").splitlines(), new, script


def test_a_jump_to_the_top_goes_last_onto_the_list_just_reloaded(tmp_path: Path):
    verbs, new, _ = _the_verbs_of_a_build_started_at_the_top(tmp_path)

    assert verbs[:2] == ["RELOAD_PLAYLIST", "SET_F_MODE 0"]
    assert [verb.startswith(f"PLAY_FILE {new}") for verb in verbs] == [False, False, True]


def test_the_jump_names_the_script_the_main_funestra_drives_the_osr2_from(tmp_path: Path):
    verbs, new, script = _the_verbs_of_a_build_started_at_the_top(tmp_path)

    assert verbs[-1] == f"PLAY_FILE {new}\t{script}"


def test_toggle_fmode_collapses_action_groups_with_provider_roots(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    portrait_root.mkdir(parents=True)
    meta = {
        "video": {"prompt": "act", "action": "Alpha", "labels": ["Alpha"], "seed": "1"},
        "source_image": {"positive_prompt": "subject", "seed": "111"},
    }
    for name in ("first.mp4", "second.mp4"):
        video = portrait_root / name
        video.write_text("x", encoding="utf-8")
        sidecar = metadata_root / "portrait" / f"{Path(name).stem}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")

    apply_fmode(
        satellites={
            FunTimeFunestra.PORTRAIT: SatelliteFmodeInputs(_channel(tmp_path, "portrait", str(portrait_root), tmp_path / "portrait_cmd.txt"), recent=False),
            FunTimeFunestra.LANDSCAPE: SatelliteFmodeInputs(_channel(tmp_path, "landscape", "", tmp_path / "landscape_cmd.txt"), recent=False),
        },
        funestras=FMODE_FUNESTRAS,
        enabled=False,
        main_build=MainListBuild(main_list=MainList(sources="", state_dir=tmp_path / "state", metadata_root=metadata_root), settings=MainListSettings(), main_funestra_cmd_file=tmp_path / "main_player_cmd.txt"),
    )

    entries = [line for line in _satellite_lines(tmp_path / "state", "portrait") if line]
    assert len(entries) == 1


def test_an_f_mode_rebuild_does_not_undo_a_satellites_own_order(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    p_old, p_new = portrait_root / "p_old.mp4", portrait_root / "p_new.mp4"
    for path, mtime in ((p_old, 1000), (p_new, 2000)):
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    apply_fmode(
        satellites={
            FunTimeFunestra.PORTRAIT: SatelliteFmodeInputs(_channel(tmp_path, "portrait", str(portrait_root), tmp_path / "portrait_cmd.txt"), recent=True),
            FunTimeFunestra.LANDSCAPE: SatelliteFmodeInputs(_channel(tmp_path, "landscape", "", tmp_path / "landscape_cmd.txt"), recent=True),
        },
        funestras=FMODE_FUNESTRAS,
        enabled=False,
        main_build=MainListBuild(main_list=MainList(sources="", state_dir=tmp_path / "state"), settings=MainListSettings(), main_funestra_cmd_file=tmp_path / "main_player_cmd.txt"),
    )

    assert _satellite_lines(tmp_path / "state", "portrait") == [str(p_new), str(p_old)]


def test_a_rebuild_can_start_the_satellite_at_the_top_of_the_new_list(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    old, new = portrait_root / "old.mp4", portrait_root / "new.mp4"
    for path, mtime in ((old, 1000), (new, 2000)):
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))
    cmd_file = tmp_path / "portrait_cmd.txt"

    apply_satellite_filter(
        2, _channel(tmp_path, "portrait", portrait_root, cmd_file),
        SatelliteBuild(str(portrait_root), recent=True), start_at_top=True,
    )

    assert cmd_file.read_text(encoding="utf-8").splitlines() == [
        "RELOAD_PLAYLIST", f"PLAY_FILE {new}",
    ]


def test_a_rebuild_leaves_the_video_on_screen_alone_by_default(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    (portrait_root / "clip.mp4").write_text("x", encoding="utf-8")
    cmd_file = tmp_path / "portrait_cmd.txt"

    apply_satellite_filter(
        2, _channel(tmp_path, "portrait", portrait_root, cmd_file),
        SatelliteBuild(str(portrait_root)),
    )

    assert cmd_file.read_text(encoding="utf-8").splitlines() == ["RELOAD_PLAYLIST"]


def _reorder(tmp_path: Path, sources: Path, *, recent: bool, query: str = "", **roots) -> None:
    apply_satellite_filter(
        2, _channel(tmp_path, "portrait", sources, tmp_path / "portrait_cmd.txt"),
        SatelliteBuild(str(sources), recent=recent, filter_query=query),
        **roots,
    )


def test_recents_orders_one_satellite_newest_first(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    old, new = portrait_root / "old.mp4", portrait_root / "new.mp4"
    for path, mtime in ((old, 1000), (new, 2000)):
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    _reorder(tmp_path, portrait_root, recent=True)

    assert _reloaded(tmp_path / "portrait_cmd.txt")
    assert not (tmp_path / "landscape_cmd.txt").exists()
    assert _satellite_lines(tmp_path / "state", "portrait") == [str(new), str(old)]


def test_recents_repicks_up_new_files(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    old = portrait_root / "old.mp4"
    old.write_text("x", encoding="utf-8")
    os.utime(old, (1000, 1000))

    _reorder(tmp_path, portrait_root, recent=True)
    assert _satellite_lines(tmp_path / "state", "portrait") == [str(old)]

    new = portrait_root / "new.mp4"
    new.write_text("x", encoding="utf-8")
    os.utime(new, (2000, 2000))
    _reorder(tmp_path, portrait_root, recent=True)
    assert _satellite_lines(tmp_path / "state", "portrait") == [str(new), str(old)]


def test_recents_collapses_action_groups_with_provider_roots(tmp_path: Path):
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    portrait_root.mkdir(parents=True)
    meta = {
        "video": {"prompt": "act", "action": "Alpha", "labels": ["Alpha"], "seed": "1"},
        "source_image": {"positive_prompt": "subject", "seed": "111"},
    }
    older, newer = portrait_root / "older.mp4", portrait_root / "newer.mp4"
    for video, mtime in ((older, 1000), (newer, 2000)):
        video.write_text("x", encoding="utf-8")
        os.utime(video, (mtime, mtime))
        sidecar = metadata_root / "portrait" / f"{video.stem}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")

    _reorder(tmp_path, portrait_root, recent=True,
             regen_metadata_root=metadata_root)

    entries = [line for line in _satellite_lines(tmp_path / "state", "portrait") if line]
    assert entries == [str(newer)], "the two-action group collapses to its newest item"


def test_an_f_mode_rebuild_does_not_undo_a_satellites_own_filter(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root, landscape_root = media_root / "portrait", media_root / "landscape"
    p_video = _make_action_video(portrait_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, metadata_root, "pt", "Twirling")
    l_twirl = _make_action_video(landscape_root, metadata_root, "lt", "Twirling")
    _make_action_video(landscape_root, metadata_root, "lc", "Alpha")

    apply_fmode(
        satellites={
            FunTimeFunestra.PORTRAIT: SatelliteFmodeInputs(_channel(tmp_path, "portrait", str(portrait_root), tmp_path / "portrait_cmd.txt"), recent=True, filter_query="alpha"),
            FunTimeFunestra.LANDSCAPE: SatelliteFmodeInputs(_channel(tmp_path, "landscape", str(landscape_root), tmp_path / "landscape_cmd.txt"), recent=True, filter_query="twirling"),
        },
        funestras=FMODE_FUNESTRAS,
        enabled=False,
        main_build=MainListBuild(main_list=MainList(sources="", state_dir=tmp_path / "state", metadata_root=metadata_root), settings=MainListSettings(), main_funestra_cmd_file=tmp_path / "main_player_cmd.txt"),
    )

    portrait = "\n".join(_satellite_lines(tmp_path / "state", "portrait"))
    landscape = "\n".join(_satellite_lines(tmp_path / "state", "landscape"))
    assert p_video in portrait
    assert "pt.mp4" not in portrait
    assert l_twirl in landscape
    assert "lc.mp4" not in landscape


def test_recents_honors_the_satellites_filter_and_orders_newest_first(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    one_seed_family, another_the_filter_cannot_fold_into_it = "scene one", "scene two"
    old = _make_action_video(portrait_root, metadata_root, "old", "Alpha", one_seed_family)
    new = _make_action_video(portrait_root, metadata_root, "new", "Alpha",
                             another_the_filter_cannot_fold_into_it)
    _make_action_video(portrait_root, metadata_root, "other", "Twirling", "scene three")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    _reorder(tmp_path, portrait_root, recent=True, query="alpha",
             regen_metadata_root=metadata_root)

    assert _satellite_lines(tmp_path / "state", "portrait") == [new, old]


def test_apply_satellite_filter_narrows_to_the_clips_with_an_enhanced_version(tmp_path: Path, monkeypatch):
    upscaled = kino_upscales_at(monkeypatch, tmp_path) / "portrait" / "provider"
    upscaled.mkdir(parents=True)
    paired, alone = upscaled / "abc_topaz.mp4", upscaled / "xyz_topaz.mp4"
    original = tmp_path / "1_sorted" / "provider" / "portrait" / "abc.mp4"
    original.parent.mkdir(parents=True)
    for clip in (paired, alone, original):
        clip.write_text("x", encoding="utf-8")

    def narrowed(level: EnhancementLevel, media_root: Path | None):
        return apply_satellite_filter(
            2, _channel(tmp_path, "portrait", upscaled, tmp_path / "portrait_cmd.txt"),
            SatelliteBuild(str(upscaled), recent=True, enhancement=level, media_root=media_root))

    assert narrowed(EnhancementLevel.ENHANCED, tmp_path).count == 1
    assert _satellite_lines(tmp_path / "state", "portrait") == [str(paired)]
    assert narrowed(EnhancementLevel.ORIGINAL, tmp_path).count == 1
    assert _satellite_lines(tmp_path / "state", "portrait") == [str(alone)]
    assert narrowed(EnhancementLevel.ENHANCED, None).applied is False
    assert _satellite_lines(tmp_path / "state", "portrait") == [str(alone)]


def test_apply_satellite_filter_reloads_only_its_cmd_file(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    p_video = _make_action_video(portrait_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, metadata_root, "pt", "Twirling")
    portrait_cmd_file = tmp_path / "portrait_cmd.txt"
    landscape_cmd_file = tmp_path / "landscape_cmd.txt"

    result = apply_satellite_filter(
        2, _channel(tmp_path, "portrait", portrait_root, portrait_cmd_file),
        SatelliteBuild(str(portrait_root), recent=True, filter_query="alpha"),
        regen_metadata_root=metadata_root,
    )

    assert result.applied is True
    assert result.count == 1
    assert _reloaded(portrait_cmd_file)
    assert not landscape_cmd_file.exists()
    portrait = "\n".join(_satellite_lines(tmp_path / "state", "portrait"))
    assert p_video in portrait
    assert "pt.mp4" not in portrait


def test_apply_satellite_filter_keeps_current_playlist_on_zero_matches(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    _make_action_video(portrait_root, metadata_root, "pt", "Twirling")
    state_dir = tmp_path / "state"
    playlist = state_dir / "portrait_playlist.tsv"
    playlist.parent.mkdir(parents=True)
    playlist.write_text("PRIOR\n", encoding="utf-8")
    cmd_file = tmp_path / "portrait_cmd.txt"

    result = apply_satellite_filter(
        2, _channel(tmp_path, "portrait", portrait_root, cmd_file),
        SatelliteBuild(str(portrait_root), recent=True, filter_query="alpha"),
        regen_metadata_root=metadata_root,
    )

    assert result.applied is False
    assert result.count == 0
    assert not cmd_file.exists()
    assert "PRIOR" in playlist.read_text(encoding="utf-8")


def test_apply_satellite_filter_clear_restores_everything(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    _make_action_video(portrait_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, metadata_root, "pt", "Twirling")

    result = apply_satellite_filter(
        2, _channel(tmp_path, "portrait", portrait_root, tmp_path / "portrait_cmd.txt"),
        SatelliteBuild(str(portrait_root), recent=True),
        regen_metadata_root=metadata_root,
    )

    assert result.applied is True
    assert result.count == 2


def test_a_satellites_browse_is_the_filter_applied_to_the_library(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    video = _make_action_video(portrait_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, metadata_root, "pt", "Twirling")

    paths = satellite_browse_paths(
        SatelliteBuild(sources=str(portrait_root), recent=True, filter_query="alpha"),
        regen_metadata_root=metadata_root,
    )

    assert video in paths
    assert not any("pt.mp4" in p for p in paths)


def test_apply_enter_omnipause_pauses_satellites_and_flags(flow_files):
    result = apply_enter_omnipause(
        omni_paused=False,
        main_mode=MainMode.GENAU,
        paused=_paused(flow_files),
        broker_cmd_file=flow_files["broker_cmd_file"],
    )

    assert result.next_omni_paused is True
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["main_funestra_paused_file"].read_text(encoding="utf-8") == "1"
    assert not flow_files["genau_cmd_file"].exists(), "the hand is the arbiter's to stop"
    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8").split() == ["PARK"]
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "1"


def test_with_no_broker_omnipause_still_freezes_the_app(flow_files):
    apply_enter_omnipause(
        omni_paused=False,
        main_mode=MainMode.GENAU,
        paused=_paused(flow_files),
    )

    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "1"
    assert not flow_files["broker_cmd_file"].exists()


def _leave_omnipause(files, *, main_mode, broker=True,
                     osr2_control=OSR2_DRIVING):
    return apply_leave_omnipause(
        omni_paused=True,
        main_mode=main_mode,
        osr2_control=osr2_control,
        paused=_paused(files),
        broker_cmd_file=files["broker_cmd_file"] if broker else None,
    )


def test_apply_leave_omnipause_in_kino_mode_resumes_main_funestra_and_lifts_the_hand(flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["main_funestra_paused_file"].write_text("1", encoding="utf-8")

    result = _leave_omnipause(flow_files, main_mode=MainMode.KINO)

    assert result.next_omni_paused is False
    assert flow_files["main_funestra_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert not flow_files["genau_cmd_file"].exists()
    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8").split() == ["RESUME"]
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "0"


def test_leaving_omnipause_in_kino_never_starts_genau_against_a_funscript_still_driving(flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["audio_paused_file"].write_text("1", encoding="utf-8")
    flow_files["main_funestra_paused_file"].write_text("1", encoding="utf-8")

    _leave_omnipause(flow_files, main_mode=MainMode.KINO)

    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert not flow_files["genau_cmd_file"].exists()
    assert flow_files["main_funestra_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "0"


def test_apply_leave_omnipause_in_genau_mode_lifts_genaus_flags_and_leaves_its_hand_to_the_arbiter(
        flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["audio_paused_file"].write_text("1", encoding="utf-8")

    _leave_omnipause(flow_files, main_mode=MainMode.GENAU, broker=False)

    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "0"
    assert not flow_files["genau_cmd_file"].exists()
    assert not flow_files["main_funestra_paused_file"].exists(), "the Main Funestra pause state untouched"
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "0"

