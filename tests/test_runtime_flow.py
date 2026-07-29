from __future__ import annotations

import os
from pathlib import Path

import pytest

import json

from fun_time.runtime_flow import (
    FMODE_PLAYERS,
    PORTRAIT_PLAYER,
    apply_enter_omnipause,
    apply_fmode,
    apply_leave_omnipause,
    apply_mode_switch,
    apply_satellite_filter,
    satellite_browse_paths,
)


def _make_action_video(
    folder: Path, media_root: Path, metadata_root: Path, name: str, action: str, prompt: str = "x"
) -> str:
    video = folder / f"{name}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")
    from fun_time.media_metadata import metadata_path_for

    sidecar = metadata_path_for(video, metadata_root)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps({"video": {"action": action, "prompt": prompt, "seed": name}}), encoding="utf-8"
    )
    return str(video)


def _satellite_lines(state_dir: Path, side: str) -> list[str]:
    """The plain video-path lines a satellite's playlist file holds.

    The native player reads a plain one-path-per-line file, with no header,
    written to ``state_dir/{side}_playlist.tsv``."""
    return (state_dir / f"{side}_playlist.tsv").read_text(encoding="utf-8").splitlines()


def _reloaded(cmd_file: Path) -> bool:
    """Whether a RELOAD_PLAYLIST verb was queued on a satellite's command file."""
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
        "nau_paused_file": tmp_path / "nau_paused.txt",
        "nau_cmd_file": tmp_path / "nau_cmd.txt",
        "broker_cmd_file": tmp_path / "broker_cmd.txt",
    }


def _mode_switch(files, *, current, target, omni_paused=False, broker=False):
    return apply_mode_switch(
        current_mode=current,
        target_mode=target,
        omni_paused=omni_paused,
        genau_paused_file=files["genau_paused_file"],
        audio_paused_file=files["audio_paused_file"],
        genau_cmd_file=files["genau_cmd_file"],
        nau_paused_file=files["nau_paused_file"],
        nau_cmd_file=files["nau_cmd_file"],
        broker_cmd_file=files["broker_cmd_file"] if broker else None,
    )


def test_nau_to_genau_resumes_genau_and_pauses_nau(flow_files):
    result = _mode_switch(flow_files, current="nau", target="genau")

    assert result.next_mode == "genau"
    assert result.is_transition is True
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME\nDISPLAY_ON"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1"


def test_genau_to_nau_pauses_genau_and_resumes_nau(flow_files):
    result = _mode_switch(flow_files, current="genau", target="nau")

    assert result.next_mode == "nau"
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "PAUSE\nDISPLAY_OFF"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"


def test_nau_to_hybrid_keeps_nau_playing(flow_files):
    # Nau already owns the display in nau; hybrid keeps it playing (Genau just
    # takes over the OSR2 and paints its HUD), so Nau's pause state is untouched.
    result = _mode_switch(flow_files, current="nau", target="hybrid")

    assert result.is_transition is True
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME\nHUD_ON\nDISPLAY_ON"
    assert not flow_files["nau_paused_file"].exists(), "Nau pause state untouched"


def _nau_cmds(files) -> list[str]:
    path = files["nau_cmd_file"]
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_every_mode_switch_tells_nau_whether_it_is_hybrid(flow_files):
    """Genau's window is a transparent layer over Nau's in hybrid and its own
    panel holds the top-left corner, so Nau has to move its own aside — and only
    this knows which mode the main slot is in."""
    _mode_switch(flow_files, current="nau", target="hybrid")
    assert "SET_HYBRID 1" in _nau_cmds(flow_files)

    _mode_switch(flow_files, current="hybrid", target="nau")
    assert "SET_HYBRID 0" in _nau_cmds(flow_files)

    _mode_switch(flow_files, current="nau", target="genau")
    assert "SET_HYBRID 0" in _nau_cmds(flow_files)


def test_every_mode_switch_tells_nau_whether_it_is_on_screen(flow_files):
    """Nau blanks in genau mode the way Genau blanks in nau mode: the idle
    main-slot player is minimized rather than closed, so without this an
    alt-tab back to it lands on the frame it was paused on."""
    _mode_switch(flow_files, current="nau", target="genau")
    assert "DISPLAY_OFF" in _nau_cmds(flow_files)

    _mode_switch(flow_files, current="hybrid", target="genau")
    assert "DISPLAY_OFF" in _nau_cmds(flow_files)

    _mode_switch(flow_files, current="genau", target="nau")
    assert "DISPLAY_ON" in _nau_cmds(flow_files)

    _mode_switch(flow_files, current="genau", target="hybrid")
    assert "DISPLAY_ON" in _nau_cmds(flow_files)


def test_leaving_hybrid_keeps_every_nau_command(flow_files):
    """The command file is overwritten, not appended, so the hybrid signal and
    the display must ride along with the T-Code re-enable, not replace it."""
    _mode_switch(flow_files, current="hybrid", target="nau")

    assert _nau_cmds(flow_files) == ["SET_HYBRID 0", "DISPLAY_ON", "SET_TCODE_ENABLED 1"]


def test_hybrid_to_nau_keeps_nau_playing(flow_files):
    result = _mode_switch(flow_files, current="hybrid", target="nau")

    assert result.next_mode == "nau"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "PAUSE\nHUD_OFF\nDISPLAY_OFF"
    assert not flow_files["nau_paused_file"].exists(), "Nau pause state untouched"


def test_hybrid_to_genau_resumes_genau(flow_files):
    # Authoritative RESUME undoes any per-video pause the hybrid arbiter applied
    # while a funscripted video was driving the OSR2.
    result = _mode_switch(flow_files, current="hybrid", target="genau")

    assert result.next_mode == "genau"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME\nHUD_OFF\nDISPLAY_ON"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1"


def test_leaving_hybrid_reenables_nau_tcode(flow_files):
    # The arbiter mutes Nau's T-Code in funscript gaps; leaving hybrid restores
    # it so a later nau mode drives its funscript again.
    for target in ("nau", "genau"):
        flow_files["nau_cmd_file"].unlink(missing_ok=True)
        _mode_switch(flow_files, current="hybrid", target=target)
        assert "SET_TCODE_ENABLED 1" in _nau_cmds(flow_files)


def test_entering_hybrid_does_not_reenable_nau_tcode(flow_files):
    """The arbiter owns that lever inside hybrid; asserting it back on here would
    fight it."""
    _mode_switch(flow_files, current="nau", target="hybrid")

    assert "SET_TCODE_ENABLED 1" not in _nau_cmds(flow_files)


def test_genau_to_hybrid_starts_nau(flow_files):
    result = _mode_switch(flow_files, current="genau", target="hybrid")

    assert result.next_mode == "hybrid"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME\nHUD_ON\nDISPLAY_ON"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"


def test_mode_switch_tells_genau_whether_it_is_on_screen(flow_files):
    # Switching away from a mode that shows Genau blanks its window, so an
    # alt-tab doesn't land on the clip frame it was resting on; switching back
    # restores it.  PAUSE alone never blanks — a paused hand still shows a clip.
    _mode_switch(flow_files, current="genau", target="nau")
    assert "DISPLAY_OFF" in flow_files["genau_cmd_file"].read_text(encoding="utf-8").split("\n")

    _mode_switch(flow_files, current="nau", target="genau")
    assert "DISPLAY_ON" in flow_files["genau_cmd_file"].read_text(encoding="utf-8").split("\n")


def test_mode_switch_during_omnipause_no_side_effects(flow_files):
    result = _mode_switch(flow_files, current="nau", target="genau", omni_paused=True)

    assert result.next_mode == "genau"
    assert result.is_transition is False
    assert not flow_files["genau_paused_file"].exists(), "Omnipause must NOT write flag files"
    assert not flow_files["nau_paused_file"].exists()
    assert not flow_files["genau_cmd_file"].exists(), "Omnipause must NOT write cmd file"


def test_genau_to_nau_writes_broker_resume(flow_files):
    _mode_switch(flow_files, current="genau", target="nau", broker=True)

    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "RESUME"


def test_hybrid_to_nau_writes_broker_resume(flow_files):
    """Leaving genau-active hybrid for nau must un-PARK the broker."""
    _mode_switch(flow_files, current="hybrid", target="nau", broker=True)

    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "RESUME"


def test_nau_to_genau_does_not_write_broker_cmd(flow_files):
    _mode_switch(flow_files, current="nau", target="genau", broker=True)

    assert not flow_files["broker_cmd_file"].exists(), "Activation must not write to broker"


def test_toggle_fmode_replaces_playlists_and_reloads_nau(tmp_path: Path):
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
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text(
        f'local_file,web_url\r\n"x","{portrait_video}"\r\n"x","{landscape_video}"\r\n',
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    portrait_cmd_file = tmp_path / "portrait_cmd.txt"
    landscape_cmd_file = tmp_path / "landscape_cmd.txt"
    nau_cmd_file = tmp_path / "nau_cmd.txt"

    result = apply_fmode(
        players=FMODE_PLAYERS,
        enabled=True,
        portrait_recent=False,
        landscape_recent=False,
        main_sources=str(primary_root),
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=favs_file,
        state_dir=state_dir,
        portrait_cmd_file=portrait_cmd_file,
        landscape_cmd_file=landscape_cmd_file,
        nau_cmd_file=nau_cmd_file,
    )

    assert result.players == FMODE_PLAYERS
    assert result.enabled is True
    # Each satellite is told to re-read its playlist file; Nau reloads via its own
    # command file too.  All three playlist files are rewritten in place.
    assert portrait_cmd_file.read_text(encoding="utf-8").splitlines() == ["RELOAD_PLAYLIST"]
    assert landscape_cmd_file.read_text(encoding="utf-8").splitlines() == ["RELOAD_PLAYLIST"]
    # …and what else rides along on Nau's write is that test's business.
    assert "RELOAD_PLAYLIST" in nau_cmd_file.read_text(encoding="utf-8").splitlines()
    assert (state_dir / "portrait_playlist.tsv").exists()
    assert (state_dir / "landscape_playlist.tsv").exists()
    assert (state_dir / "nau_playlist.tsv").exists()


def test_fmode_on_one_player_leaves_the_others_playlists_untouched(tmp_path: Path):
    """F-mode is per player, so naming one must rebuild that one alone.

    Rebuilding a player that was not asked for would reshuffle its queue out from
    under it — which is exactly what the old all-three build did, and the reason
    the rebuild is per player now.
    """
    portrait_root, landscape_root = tmp_path / "portrait", tmp_path / "landscape"
    for root in (portrait_root, landscape_root):
        root.mkdir(parents=True)
        (root / "clip.mp4").write_text("x", encoding="utf-8")
    state_dir = tmp_path / "state"
    landscape_cmd_file = tmp_path / "landscape_cmd.txt"
    nau_cmd_file = tmp_path / "nau_cmd.txt"

    result = apply_fmode(
        players=(PORTRAIT_PLAYER,),
        enabled=True,
        portrait_recent=False,
        landscape_recent=False,
        main_sources="",
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        portrait_cmd_file=tmp_path / "portrait_cmd.txt",
        landscape_cmd_file=landscape_cmd_file,
        nau_cmd_file=nau_cmd_file,
    )

    assert result.players == (PORTRAIT_PLAYER,)
    assert (state_dir / "portrait_playlist.tsv").exists()
    assert not (state_dir / "landscape_playlist.tsv").exists()
    assert not (state_dir / "nau_playlist.tsv").exists()
    assert not landscape_cmd_file.exists()
    assert not nau_cmd_file.exists()


def test_toggle_fmode_tells_nau_the_flag_on_the_same_write_as_the_reload(tmp_path: Path):
    """Nau cannot read F-mode off the playlist it is handed — a list of scripted
    videos looks like any other — so its HUD only knows because it is told.

    It has to ride along with the reload rather than follow it: the command file is
    overwritten, not appended, so a second write would drop the first verb.
    """
    root = tmp_path / "videos" / "videos" / "primary"
    root.mkdir(parents=True)
    (root / "main.mp4").write_text("x", encoding="utf-8")
    nau_cmd_file = tmp_path / "nau_cmd.txt"

    def told(enabled: bool) -> list[str]:
        apply_fmode(
            players=FMODE_PLAYERS,
            enabled=enabled,
            portrait_recent=False, landscape_recent=False,
            main_sources=str(root), portrait_sources="", landscape_sources="",
            favs_file=tmp_path / "favs.csv", state_dir=tmp_path / "state",
            portrait_cmd_file=tmp_path / "p_cmd.txt",
            landscape_cmd_file=tmp_path / "l_cmd.txt",
            nau_cmd_file=nau_cmd_file,
        )
        return nau_cmd_file.read_text(encoding="utf-8").splitlines()

    assert told(True) == ["RELOAD_PLAYLIST", "SET_F_MODE 1"]
    assert told(False) == ["RELOAD_PLAYLIST", "SET_F_MODE 0"]


def test_toggle_fmode_collapses_action_groups_with_provider_roots(tmp_path: Path):
    """With the provider roots supplied, the rebuilt satellite playlists collapse
    same-source-image action groups to one entry."""
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    portrait_root.mkdir(parents=True)
    meta = {
        "video": {"prompt": "act", "action": "Alpha", "seed": "1"},
        "source_image": {"positive_prompt": "subject", "seed": "111"},
    }
    for name in ("first.mp4", "second.mp4"):
        video = portrait_root / name
        video.write_text("x", encoding="utf-8")
        sidecar = metadata_root / "portrait" / f"{Path(name).stem}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")

    apply_fmode(
        players=FMODE_PLAYERS,
        enabled=False,
        portrait_recent=False,
        landscape_recent=False,
        main_sources="",
        portrait_sources=str(portrait_root),
        landscape_sources="",
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        portrait_cmd_file=tmp_path / "portrait_cmd.txt",
        landscape_cmd_file=tmp_path / "landscape_cmd.txt",
        nau_cmd_file=tmp_path / "nau_cmd.txt",
        regen_metadata_root=metadata_root,
    )

    entries = [line for line in _satellite_lines(tmp_path / "state", "portrait") if line]
    assert len(entries) == 1


def test_toggle_fmode_preserves_recency_ordering(tmp_path: Path):
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    p_old, p_new = portrait_root / "p_old.mp4", portrait_root / "p_new.mp4"
    for path, mtime in ((p_old, 1000), (p_new, 2000)):
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    # Turning F-mode off must keep the satellite playlists newest-first, not reshuffle.
    apply_fmode(
        players=FMODE_PLAYERS,
        enabled=False,
        portrait_recent=True,
        landscape_recent=True,
        main_sources="",
        portrait_sources=str(portrait_root),
        landscape_sources="",
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        portrait_cmd_file=tmp_path / "portrait_cmd.txt",
        landscape_cmd_file=tmp_path / "landscape_cmd.txt",
        nau_cmd_file=tmp_path / "nau_cmd.txt",
    )

    assert _satellite_lines(tmp_path / "state", "portrait") == [str(p_new), str(p_old)]


def test_a_rebuild_can_start_the_side_at_the_top_of_the_new_list(tmp_path: Path):
    """"Latest" means "show me what has just arrived", and a plain reload keeps the
    clip on screen and carries on from where it sat — so the new order only ever
    applied *behind* it and the newest clips were never reached.  A caller that means
    "start over" asks for the head, which the player takes as PLAY_FILE."""
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    old, new = portrait_root / "old.mp4", portrait_root / "new.mp4"
    for path, mtime in ((old, 1000), (new, 2000)):
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))
    cmd_file = tmp_path / "portrait_cmd.txt"

    apply_satellite_filter(
        which=2, query="", f_mode_enabled=False, recent=True, start_at_top=True,
        sources=str(portrait_root), favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state", cmd_file=cmd_file,
    )

    assert cmd_file.read_text(encoding="utf-8").splitlines() == [
        "RELOAD_PLAYLIST", f"PLAY_FILE {new}",
    ]


def test_a_rebuild_leaves_the_clip_on_screen_alone_by_default(tmp_path: Path):
    """A filter change is not a "start over": the reload keeps the clip playing while
    it survives, so only a caller that asks gets moved to the head."""
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    (portrait_root / "clip.mp4").write_text("x", encoding="utf-8")
    cmd_file = tmp_path / "portrait_cmd.txt"

    apply_satellite_filter(
        which=2, query="", f_mode_enabled=False, recent=False,
        sources=str(portrait_root), favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state", cmd_file=cmd_file,
    )

    assert cmd_file.read_text(encoding="utf-8").splitlines() == ["RELOAD_PLAYLIST"]


def _reorder(tmp_path: Path, sources: Path, *, recent: bool, query: str = "", **roots) -> None:
    """Reload the portrait satellite in one order — what "latest"/"shuffle" run."""
    apply_satellite_filter(
        which=2,
        query=query,
        f_mode_enabled=False,
        recent=recent,
        sources=str(sources),
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        cmd_file=tmp_path / "portrait_cmd.txt",
        **roots,
    )


def test_recents_orders_one_satellite_newest_first(tmp_path: Path):
    """Recents is a sided command now — it reloads the satellite it names, and only
    that one, newest-first."""
    portrait_root = tmp_path / "portrait"
    portrait_root.mkdir(parents=True)
    old, new = portrait_root / "old.mp4", portrait_root / "new.mp4"
    for path, mtime in ((old, 1000), (new, 2000)):
        path.write_text("x", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    _reorder(tmp_path, portrait_root, recent=True)

    assert _reloaded(tmp_path / "portrait_cmd.txt")
    assert not (tmp_path / "landscape_cmd.txt").exists()  # the other side is untouched
    assert _satellite_lines(tmp_path / "state", "portrait") == [str(new), str(old)]


def test_recents_repicks_up_new_files(tmp_path: Path):
    """A repeat press rescans the sources so newly-arrived files land on top."""
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
    """Recents honours action groups too: with the provider roots supplied,
    same-source-image clips collapse to one entry, its newest member."""
    media_root = tmp_path / "videos" / "videos"
    metadata_root = tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    portrait_root.mkdir(parents=True)
    meta = {
        "video": {"prompt": "act", "action": "Alpha", "seed": "1"},
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
             regen_media_root=media_root, regen_metadata_root=metadata_root)

    entries = [line for line in _satellite_lines(tmp_path / "state", "portrait") if line]
    assert entries == [str(newer)], "the two-action group collapses to its newest member"


def test_toggle_fmode_applies_per_satellite_metadata_filters(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root, landscape_root = media_root / "portrait", media_root / "landscape"
    p_clip = _make_action_video(portrait_root, media_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, media_root, metadata_root, "pk", "Kissing")
    l_kiss = _make_action_video(landscape_root, media_root, metadata_root, "lk", "Kissing")
    _make_action_video(landscape_root, media_root, metadata_root, "lc", "Alpha")

    apply_fmode(
        players=FMODE_PLAYERS,
        enabled=False,  # F-mode OFF, so only the metadata filter applies
        portrait_recent=True,
        landscape_recent=True,
        main_sources="",
        portrait_sources=str(portrait_root),
        landscape_sources=str(landscape_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        portrait_cmd_file=tmp_path / "portrait_cmd.txt",
        landscape_cmd_file=tmp_path / "landscape_cmd.txt",
        nau_cmd_file=tmp_path / "nau_cmd.txt",
        regen_metadata_root=metadata_root,
        portrait_filter="alpha",
        landscape_filter="kissing",
    )

    portrait = "\n".join(_satellite_lines(tmp_path / "state", "portrait"))
    landscape = "\n".join(_satellite_lines(tmp_path / "state", "landscape"))
    assert p_clip in portrait and "pk.mp4" not in portrait
    assert l_kiss in landscape and "lc.mp4" not in landscape


def test_recents_honours_the_sides_filter_and_orders_newest_first(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    # Distinct prompts keep the two Alphas in distinct seed families, so the
    # filtered build keeps both and the newest-first order is visible.
    old = _make_action_video(portrait_root, media_root, metadata_root, "old", "Alpha", "scene one")
    new = _make_action_video(portrait_root, media_root, metadata_root, "new", "Alpha", "scene two")
    _make_action_video(portrait_root, media_root, metadata_root, "other", "Kissing", "scene three")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    _reorder(tmp_path, portrait_root, recent=True, query="alpha",
             regen_media_root=media_root, regen_metadata_root=metadata_root)

    assert _satellite_lines(tmp_path / "state", "portrait") == [new, old]  # filtered to alpha, newest-first


def test_apply_satellite_filter_reloads_only_its_cmd_file(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    p_clip = _make_action_video(portrait_root, media_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, media_root, metadata_root, "pk", "Kissing")
    portrait_cmd_file = tmp_path / "portrait_cmd.txt"
    landscape_cmd_file = tmp_path / "landscape_cmd.txt"

    result = apply_satellite_filter(
        which=2,
        query="alpha",
        f_mode_enabled=False,
        recent=True,
        sources=str(portrait_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        cmd_file=portrait_cmd_file,
        regen_media_root=media_root,
        regen_metadata_root=metadata_root,
    )

    assert result.applied is True
    assert result.count == 1
    # Only the targeted satellite's command file gets a reload.
    assert _reloaded(portrait_cmd_file)
    assert not landscape_cmd_file.exists()
    portrait = "\n".join(_satellite_lines(tmp_path / "state", "portrait"))
    assert p_clip in portrait and "pk.mp4" not in portrait


def test_apply_satellite_filter_keeps_current_playlist_on_zero_matches(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    _make_action_video(portrait_root, media_root, metadata_root, "pk", "Kissing")
    state_dir = tmp_path / "state"
    playlist = state_dir / "portrait_playlist.tsv"
    playlist.parent.mkdir(parents=True)
    playlist.write_text("PRIOR\n", encoding="utf-8")
    cmd_file = tmp_path / "portrait_cmd.txt"

    result = apply_satellite_filter(
        which=2,
        query="alpha",
        f_mode_enabled=False,
        recent=True,
        sources=str(portrait_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        cmd_file=cmd_file,
        regen_media_root=media_root,
        regen_metadata_root=metadata_root,
    )

    assert result.applied is False
    assert result.count == 0
    assert not cmd_file.exists()  # no reload
    assert "PRIOR" in playlist.read_text(encoding="utf-8")  # left in place, not rebuilt


def test_apply_satellite_filter_clear_restores_everything(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    _make_action_video(portrait_root, media_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, media_root, metadata_root, "pk", "Kissing")

    result = apply_satellite_filter(
        which=2,
        query="",
        f_mode_enabled=False,
        recent=True,
        sources=str(portrait_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        cmd_file=tmp_path / "portrait_cmd.txt",
        regen_media_root=media_root,
        regen_metadata_root=metadata_root,
    )

    assert result.applied is True
    assert result.count == 2


def test_satellite_browse_paths_returns_the_filtered_browse(tmp_path: Path):
    """The pure browse builder "no loop" reshapes the queue back to: it honours
    the satellite's filter and returns the paths, with no file to touch."""
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    portrait_root = media_root / "portrait"
    clip = _make_action_video(portrait_root, media_root, metadata_root, "pc", "Alpha")
    _make_action_video(portrait_root, media_root, metadata_root, "pk", "Kissing")

    paths = satellite_browse_paths(
        which=2,
        query="alpha",
        f_mode_enabled=False,
        recent=True,
        sources=str(portrait_root),
        favs_file=tmp_path / "favs.csv",
        state_dir=tmp_path / "state",
        regen_metadata_root=metadata_root,
    )

    assert clip in paths
    assert not any("pk.mp4" in p for p in paths)


def test_apply_enter_omnipause_pauses_satellites_and_flags(flow_files):
    result = apply_enter_omnipause(
        omni_paused=False,
        main_mode="genau",
        portrait_paused_file=flow_files["portrait_paused_file"],
        landscape_paused_file=flow_files["landscape_paused_file"],
        genau_paused_file=flow_files["genau_paused_file"],
        audio_paused_file=flow_files["audio_paused_file"],
        genau_cmd_file=flow_files["genau_cmd_file"],
        nau_paused_file=flow_files["nau_paused_file"],
        broker_cmd_file=flow_files["broker_cmd_file"],
    )

    assert result.next_omni_paused is True
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "PAUSE"
    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "PARK"
    # Both satellites are frozen via their paused flag file — a paused native
    # satellite simply cannot auto-advance, so no HTTP re-pause is needed.
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "1"


def test_apply_enter_omnipause_relief_retracts_and_still_freezes_everything(flow_files):
    """Relief freezes the session exactly as a plain enter does — only the OSR2's
    destination changes, from home to the far end of its stroke."""
    result = apply_enter_omnipause(
        omni_paused=False,
        main_mode="hybrid",
        portrait_paused_file=flow_files["portrait_paused_file"],
        landscape_paused_file=flow_files["landscape_paused_file"],
        genau_paused_file=flow_files["genau_paused_file"],
        audio_paused_file=flow_files["audio_paused_file"],
        genau_cmd_file=flow_files["genau_cmd_file"],
        nau_paused_file=flow_files["nau_paused_file"],
        broker_cmd_file=flow_files["broker_cmd_file"],
        relief=True,
    )

    assert result.next_omni_paused is True
    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "RETRACT"
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "PAUSE"
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "1"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "1"


def _leave_omnipause(files, *, main_mode, broker=True):
    return apply_leave_omnipause(
        omni_paused=True,
        main_mode=main_mode,
        portrait_paused_file=files["portrait_paused_file"],
        landscape_paused_file=files["landscape_paused_file"],
        genau_paused_file=files["genau_paused_file"],
        audio_paused_file=files["audio_paused_file"],
        genau_cmd_file=files["genau_cmd_file"],
        nau_paused_file=files["nau_paused_file"],
        broker_cmd_file=files["broker_cmd_file"] if broker else None,
    )


def test_apply_leave_omnipause_in_nau_mode_resumes_nau(flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["nau_paused_file"].write_text("1", encoding="utf-8")

    result = _leave_omnipause(flow_files, main_mode="nau")

    assert result.next_omni_paused is False
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"
    # Genau stays paused when main_mode is nau
    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "1"
    assert not flow_files["genau_cmd_file"].exists()
    # Broker is un-PARKed regardless of mode
    assert flow_files["broker_cmd_file"].read_text(encoding="utf-8") == "RESUME"
    # Both satellites are unfrozen via their paused flag file.
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "0"


def test_apply_leave_omnipause_in_hybrid_leaves_genaus_stroke_to_the_arbiter(flow_files):
    """Hybrid hands the OSR2 between the funscript and Genau per stretch, and the
    arbiter re-asserts that on its next tick.  Resuming Genau's stroke here too
    started it against a funscript that was still driving — both on the device at
    once, which the user felt as the OSR2 fighting itself."""
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["audio_paused_file"].write_text("1", encoding="utf-8")
    flow_files["nau_paused_file"].write_text("1", encoding="utf-8")

    _leave_omnipause(flow_files, main_mode="hybrid")

    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert not flow_files["genau_cmd_file"].exists()
    # Hybrid displays Nau, so Nau resumes too (Genau just drives the OSR2).
    assert flow_files["nau_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "0"


def test_apply_leave_omnipause_in_genau_mode_resumes_genau_only(flow_files):
    flow_files["genau_paused_file"].write_text("1", encoding="utf-8")
    flow_files["audio_paused_file"].write_text("1", encoding="utf-8")

    result = _leave_omnipause(flow_files, main_mode="genau", broker=False)

    assert flow_files["genau_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["audio_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["genau_cmd_file"].read_text(encoding="utf-8") == "RESUME"
    assert not flow_files["nau_paused_file"].exists(), "Nau pause state untouched"
    # Both satellites are unfrozen regardless of the main player mode.
    assert flow_files["portrait_paused_file"].read_text(encoding="utf-8") == "0"
    assert flow_files["landscape_paused_file"].read_text(encoding="utf-8") == "0"
