from __future__ import annotations

import json
import logging
from functools import partial
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from funestra import funestra_controls
from funestra.flick_folder import flat_flicks_in
from funestra.funestra import Channels, Funestra
from funestra.funestra_controls import FunestraControls
from funestra.funestra_controls import apply_command as apply_satellite_command
from funestra.funestra_setup import FunestraSetup
from funestra.funestra_verbs import SHOW
from funestra.funscript_jumps import FunscriptJumps
from funestra.genau_controls import VERBS as GENAU_VERBS
from funestra.messages import MessageWriter
from funestra.modes import MainMode
from funestra.playback import Playback
from funestra.playlist import read_playlist
from funestra.testing import FakeEngine
from shared_ui.orientation import PORTRAIT

from fun_time.bridge_records import Op
from fun_time.command_dispatch import dispatch_command
from fun_time.dashboard_actions import BROWSE_LIBRARY_CLOSE, HELP_REFERENCE_COMMANDS
from fun_time.genau_config import GenauSettings
from fun_time.mode_plan import MAIN_KINO_MODE
from fun_time.modes import PLAYLIST_PORTRAIT, build_playlist_file_path
from fun_time.origenerator_switches import KINO_MODE as SATELLITE_KINO_MODE
from fun_time.origenerator_switches import ORIGENERATOR_MODE
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.windows_bridge_dispatch_loop import HANDOFF_COMMANDS
from fun_time_vr import headset_verbs
from fun_time_vr.genau_in_the_headset import GenauInTheHeadset
from fun_time_vr.headset_verbs import HeadsetVerbs
from main_funestra import controls as main_funestra_controls
from main_funestra import genau_verbs as genaus_verbs_on_the_main_funestra
from main_funestra.clip_nav import ClipNav
from main_funestra.genau import GenauChannels, GenauFlicks, GenauOutputs, GenauSeams
from main_funestra.kino import Kino, KinoLibrary
from main_funestra.mode_memory import ModeMemory, RememberedMode
from tests.dispatch_sweep import every_command, headset_config, sweep
from tests.genau_fakes import FakeNotifier, FakeSink
from tests.test_windows_bridge_dispatch_loop import make_runner

_OPS_WITH_NO_WINDOWS = frozenset({
    Op.MESSAGE, Op.SHOW_WINDOW, Op.HIDE_WINDOW, Op.ACTIVATE_WINDOW, Op.MINIMIZE_WINDOW,
    Op.RESTORE_PARKED, Op.RESTACK_RFB,
    Op.DISABLE_ALL_TOPMOST, Op.RESTORE_ALL_TOPMOST,
})

_OPS_THAT_STILL_ACT = frozenset({
    Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS, Op.SAVE_CLIP, Op.OPEN_RFB_TAB,
    Op.HAND_OVER_FUNESTRAS, Op.TAKE_BACK_FUNESTRAS, Op.FOLLOW_THE_LOCK, Op.IGNORED, Op.NARROWING_ANSWERS,
})

_OPS_A_HEADSET_NEVER_RAISES = frozenset({Op.MAIN_FUNESTRA_ANSWERS})


@pytest.fixture(scope="module")
def landed(tmp_path_factory) -> dict[str, dict[str, list[str]]]:
    return sweep(headset_config(tmp_path_factory.mktemp("vr_parity")),
                 (SATELLITE_KINO_MODE, ORIGENERATOR_MODE))


@pytest.fixture(scope="module")
def landed_hosting_none(tmp_path_factory) -> dict[str, dict[str, list[str]]]:
    root = tmp_path_factory.mktemp("vr_parity_hosting_none")
    return sweep(headset_config(root, names_an_origenerator=False), (ORIGENERATOR_MODE,))


def test_the_sweep_sends_every_funestra_its_ten_second_steps(landed):
    for channel in ("main_funestra_cmd_file", "portrait_cmd_file", "landscape_cmd_file"):
        assert {"SEEK_FWD", "SEEK_BACK"} <= set(_sent_to(landed, channel)), channel


def _sent_to(landed, channel: str) -> dict[str, str]:
    seen: dict[str, str] = {}
    for where, written in landed.items():
        for line in written.get(channel, ()):
            seen.setdefault(line.split(None, 1)[0].upper(), where)
    return seen


WINDOW = (640, 480)


class _TheHeadsetsMainFunestra:
    def __init__(self, tmp_path: Path) -> None:
        videos = tmp_path / "videos"
        videos.mkdir(exist_ok=True)
        first, second = videos / "one.mp4", videos / "two.mp4"
        for video in (first, second):
            video.write_bytes(b"")
        script = videos / "one.funscript"
        script.write_text(json.dumps({"actions": [{"at": 0, "pos": 0}]}), encoding="utf-8")
        channels = Channels(
            playlist=tmp_path / "main_player_playlist.tsv",
            command=tmp_path / "main_player_cmd.txt",
            console=tmp_path / "main_player_console.json",
            dashboard_cmd=tmp_path / "dashboard_cmd.txt",
        )
        channels.playlist.write_text(f"{first}\t{script}\n{second}\t\n", encoding="utf-8")
        self._command_file = channels.command
        self.engine = FakeEngine(duration_ms=3_600_000.0)
        self.funestra = Funestra(self.engine, FunestraSetup(
            channels=channels, playlist=read_playlist(channels.playlist),
            locked=True, sound_is_the_rooms=True,
            users={MainMode.KINO: partial(
                Kino, library=KinoLibrary(
                    source=None, clip_nav=ClipNav.build([], None), resolve_playlist=list),
                messages=MessageWriter(None), memory=ModeMemory(None),
                remembered=RememberedMode()),
                MainMode.GENAU: lambda _playback: _genau_in_the_headset(tmp_path)},
            window_verbs=self._apply_the_headsets_verb,
        ))
        self.verbs = HeadsetVerbs(self.funestra.playback, metadata_root=tmp_path / "metadata",
                                  vr_dirs=())

    def _apply_the_headsets_verb(self, command: str) -> bool:
        return self.verbs.apply(command)

    def answers(self, line: str) -> bool:
        self._command_file.write_text(line + "\n", encoding="utf-8")
        with _the_funestras_refusals() as refused:
            self.funestra.tick(viewport=WINDOW)
        return not refused


def _genau_in_the_headset(tmp_path: Path) -> GenauInTheHeadset:
    flicks = tmp_path / "flicks"
    flat = flat_flicks_in(flicks)
    flat.mkdir(parents=True, exist_ok=True)
    (flat / "made up flick.mp4").write_bytes(b"flick")
    state = tmp_path / "genau"
    return GenauInTheHeadset(
        GenauFlicks(folder=flicks), settings=GenauSettings(shuffle_on_load=False),
        channels=GenauChannels(command=state / "genau_cmd.txt", paused=state / "genau_paused.txt",
                               status=state / "genau_status.txt", drive=state / "genau_drive.txt"),
        outputs=GenauOutputs(notifier=FakeNotifier(), tcode_sink=FakeSink()),
        seams=GenauSeams(
            decode=lambda _flick: [np.zeros((8, 16, 3), dtype=np.uint8)],
            start_thread=_every_thread_but_the_beats_reader_at_once))


def _every_thread_but_the_beats_reader_at_once(*, target, args=(), name=""):
    if name != "genau-udp":
        target(*args)


class _the_funestras_refusals:
    def __enter__(self):
        self._handler = _Refusals()
        logging.getLogger("funestra.funestra").addHandler(self._handler)
        return self._handler.refused

    def __exit__(self, *_exc):
        logging.getLogger("funestra.funestra").removeHandler(self._handler)


class _Refusals(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.refused: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if "Unhandled command" in record.getMessage():
            self.refused.append(record.getMessage())


class TestTheMainFunestra:
    def test_every_verb_it_is_sent_is_one_it_answers(self, landed, tmp_path):
        window = _TheHeadsetsMainFunestra(tmp_path)
        dead = {
            verb: where
            for verb, where in _sent_to(landed, "main_funestra_cmd_file").items()
            if not window.answers(_a_whole_line(verb))
        }
        assert not dead, f"the headset's Main Funestra answers none of these: {dead}"

    def test_a_reset_unlocks_it_and_puts_its_speed_back_to_normal(self, tmp_path):
        config = headset_config(tmp_path)
        window = _TheHeadsetsMainFunestra(tmp_path)
        window.answers("SPEED_DOWN")

        dispatch_command("main_reset", BridgeState(main_mode=MAIN_KINO_MODE), config)
        for line in config.main_funestra_cmd_file.read_text(encoding="utf-8").splitlines():
            window.answers(line)

        playback = window.funestra.playback
        assert (playback.is_locked, playback.speed) == (False, 1.0)


def _a_whole_line(verb: str) -> str:
    if verb not in _VERBS_THAT_TAKE_A_VALUE:
        return verb
    return f"{verb} {_WORDS_FOR.get(verb, 1)}"


_WORDS_FOR = {SHOW: MAIN_KINO_MODE,
              genaus_verbs_on_the_main_funestra.SET_GENAU_ORIENTATIONS: PORTRAIT}


_VERBS_THAT_TAKE_A_VALUE = frozenset(
    spelling
    for registry in (main_funestra_controls.VERBS, genaus_verbs_on_the_main_funestra.VERBS,
                     headset_verbs.VERBS, funestra_controls.VERBS)
    for spelling, (_control, verb) in registry.items()
    if verb.takes_a_value
)


class TestTheSatellites:
    def test_every_verb_they_are_sent_is_one_the_session_answers(self, landed, tmp_path):
        videos = tmp_path / "sat"
        videos.mkdir()
        videos = [videos / "a.mp4", videos / "b.mp4"]
        for video in videos:
            video.write_bytes(b"")
        dead: dict[str, str] = {}
        for channel in ("portrait_cmd_file", "landscape_cmd_file"):
            for verb, where in _sent_to(landed, channel).items():
                session = Playback(list(videos), engine=FakeEngine())
                handled = apply_satellite_command(
                    _a_satellite_line(verb, videos[0]),
                    FunestraControls(session, reload_playlist=lambda: None,
                                     funscript_jumps=FunscriptJumps(
                                         session, MessageWriter(None))),
                )
                if not handled:
                    dead[verb] = where
        assert not dead, f"the hosted satellite session answers none of these: {dead}"

    def test_a_reset_unlocks_it_and_starts_a_fresh_browse_from_the_top(self, tmp_path):
        config = headset_config(tmp_path)
        videos = [tmp_path / "portrait" / f"{name}.mp4" for name in ("alpha", "beta", "gamma")]
        for video in videos:
            video.write_bytes(b"")
        session = Playback(list(videos), engine=FakeEngine())
        session.set_locked(True)
        playlist = build_playlist_file_path(config.state_dir, PLAYLIST_PORTRAIT)

        dispatch_command(
            "portrait_reset", BridgeState(portrait=SatelliteState(locked=True, latest=True)), config)
        controls = FunestraControls(
            session,
            reload_playlist=lambda: session.replace_playlist(
                [item.path for item in read_playlist(playlist)]),
        )
        for line in config.portrait_cmd_file.read_text(encoding="utf-8").splitlines():
            apply_satellite_command(line, controls)

        assert not session.is_locked
        assert session.current_video == read_playlist(playlist)[0].path


def _a_satellite_line(verb: str, video: Path) -> str:
    return f"{verb} {video}" if verb == "PLAY_FILE" else _a_whole_line(verb)


class TestGenau:
    def test_every_verb_it_is_sent_is_in_the_registry(self, landed):
        dead = {
            verb: where
            for verb, where in _sent_to(landed, "genau_cmd_file").items()
            if verb not in GENAU_VERBS
        }
        assert not dead, f"funestra's control registry answers none of these: {dead}"


class TestTheHostedOrigenerator:
    def test_it_is_sent_what_is_said_to_a_satellite_in_its_mode(self, landed):
        assert landed["kino/origenerator/portrait_next"]["origenerator_cmd_file"] == [
            "portrait_next"]

    def test_a_lock_locks_the_funestra_and_then_tells_the_hosted_app(self, landed):
        assert landed["kino/origenerator/portrait_lock"] == {
            "portrait_cmd_file": ["LOCK_ON"], "origenerator_cmd_file": ["portrait_lock"],
            "__ops__": []}


_THE_LOOPS_OWN = frozenset({
    "quit", *HANDOFF_COMMANDS, *HELP_REFERENCE_COMMANDS,
    "omniminimize", "omnirestore", "omnipause_toggle", "enter_omnipause",
    "relief_omnipause", "pause", "play", "browse_library", BROWSE_LIBRARY_CLOSE,
    "broker_panel", "broker_start", "broker_stop", "voice_off", "voice_toggle",
    "voice_accept",
})


class TestTheLoopInFrontOfTheDispatch:
    def test_every_other_command_reaches_the_dispatch_as_it_was_said(self, tmp_path):
        runner = make_runner(tmp_path)
        rewritten = {}
        for command in every_command():
            if command in _THE_LOOPS_OWN:
                continue
            with patch.object(runner, "_dispatch") as dispatched:
                runner._handle_command(command)
            if [call.args for call in dispatched.call_args_list] != [(command, None)]:
                rewritten[command] = [call.args for call in dispatched.call_args_list]
        assert not rewritten, f"the loop hands the dispatch these as something else: {rewritten}"


class TestWhatAHeadsetDoesNotHost:
    def test_a_config_naming_no_origenerator_sends_one_nothing(self, landed_hosting_none):
        routed = {
            where: written["origenerator_cmd_file"]
            for where, written in landed_hosting_none.items()
            if written.get("origenerator_cmd_file")
        }
        assert not routed, f"these reached an Origenerator the config names none of: {routed}"

    def test_every_window_op_raised_is_one_a_headset_has_an_answer_for(self, landed):
        known = _OPS_WITH_NO_WINDOWS | _OPS_THAT_STILL_ACT
        unclassified = {
            op: where
            for where, written in landed.items()
            for op in written.get("__ops__", ())
            if op not in known
        }
        assert not unclassified, (
            f"window ops a VR session neither acts on nor states as a no-op: {unclassified}"
        )

    def test_nothing_it_sends_waits_on_an_answer_its_main_funestra_never_gives(self, landed):
        waiting = {
            where for where, written in landed.items()
            if set(written.get("__ops__", ())) & _OPS_A_HEADSET_NEVER_RAISES
        }
        assert not waiting, f"these wait on the headset's Main Funestra to answer: {waiting}"

    def test_the_op_sets_between_them_loading_screen_the_whole_vocabulary(self):
        placed = (_OPS_WITH_NO_WINDOWS, _OPS_THAT_STILL_ACT, _OPS_A_HEADSET_NEVER_RAISES)
        assert set(Op) == frozenset().union(*placed)
        assert sum(map(len, placed)) == len(set(Op))
