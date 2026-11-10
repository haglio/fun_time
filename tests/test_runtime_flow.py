from __future__ import annotations

import os
from pathlib import Path

import pytest
from funestra.main_hud import OSR2_DRIVING
from funestra.modes import MainMode

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.main_list import MainList, MainListSettings
from fun_time.main_list_builds import BUILDS_HERE, MainListBuild
from fun_time.omnipause import PausedFiles, apply_enter_omnipause, apply_leave_omnipause
from fun_time.runtime_flow import (
    FMODE_FUNESTRAS,
    apply_fmode,
    apply_mode_switch,
)


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


def test_toggle_fmode_rebuilds_and_reloads_the_main_funestras_list(tmp_path: Path):
    primary_root = tmp_path / "videos" / "videos" / "primary"
    primary_root.mkdir(parents=True)
    (primary_root / "main.mp4").write_text("x", encoding="utf-8")
    main_funestra_cmd_file = tmp_path / "main_player_cmd.txt"

    result = apply_fmode(
        funestras=FMODE_FUNESTRAS,
        enabled=True,
        main_build=MainListBuild(main_list=MainList(sources=str(primary_root), state_dir=tmp_path / "state"), settings=MainListSettings(), main_funestra_cmd_file=main_funestra_cmd_file),
    )

    assert (result.funestras, result.enabled) == ((FunTimeFunestra.MAIN,), True)
    assert "RELOAD_PLAYLIST" in main_funestra_cmd_file.read_text(encoding="utf-8").splitlines()
    assert (tmp_path / "state" / "main_player_playlist.tsv").exists()


def test_f_mode_is_told_right_behind_the_reload(tmp_path: Path):
    root = tmp_path / "videos" / "videos" / "primary"
    root.mkdir(parents=True)
    (root / "main.mp4").write_text("x", encoding="utf-8")
    main_funestra_cmd_file = tmp_path / "main_player_cmd.txt"

    def told(enabled: bool) -> list[str]:
        main_funestra_cmd_file.unlink(missing_ok=True)
        apply_fmode(
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

