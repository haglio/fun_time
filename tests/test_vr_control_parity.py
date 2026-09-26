"""Every control the session can send, answered by something a VR session hosts.

A headset runs the same orchestrator, the same dispatch loop, the same AHK
hotkey script and the same voice control as the desktop.  What differs is who
is listening at the far end of each file channel: the main player is
:class:`fun_time_vr.roles.MainRole` rather than the main player, Genau and both satellites
live inside the one VR process, the hosted Origenerator answers a side in its
mode as it does on the desktop, and the windows the desktop's ops act on do not
exist.  So a control can be perfectly routed and still be dead in the headset —
which is exactly how the main-slot padlock and F-mode's status line came to be
dead there with the whole suite green.

This module closes that.  It walks every command the reference can produce —
every hotkey, every spoken phrase — through the real dispatch, against the
config a headset session builds and in both of the satellite side's modes, and
holds each verb that lands to the vocabulary of whatever will actually read it
in a VR session.  The only way to leave a control dead in the headset is to
name it, with its reason, in
:data:`fun_time_vr.roles.UNIMPLEMENTED_MAIN_PLAYER_VERBS` or in one of the sets here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from player_core.genau_controls import VERBS as GENAU_VERBS
from player_core.playlist import read_playlist

from fun_time.bridge_records import BridgeConfig, Op
from fun_time.command_dispatch import dispatch_command
from fun_time.command_reference import build_reference_sections
from fun_time.config import load_config
from fun_time.manifest import LaunchManifest, write_manifest_data
from fun_time.mode_plan import MAIN_MODES, MAIN_VIDEO_MODE
from fun_time.modes import PLAYLIST_PORTRAIT, build_playlist_file_path
from fun_time.players import Player
from fun_time.satellites_mode import ORIGENERATOR_MODE
from fun_time.satellites_mode import VIDEO_MODE as SATELLITE_VIDEO_MODE
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.voice_commands import VOICE_COMMANDS
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time_vr import roles
from fun_time_vr.orchestrator import build_vr_manifest
from fun_time_vr.roles import UNIMPLEMENTED_MAIN_PLAYER_VERBS, MainRole
from main_player import controls as main_player_controls
from satellite.runtime import SatelliteControls
from satellite.runtime import apply_command as apply_satellite_command
from satellite.session import SatelliteSession
from tests.origenerator_contract import answers
from tests.satellite_fakes import FakeSatellitePlayer
from tests.test_vr_roles import FakeDriver, FakePlayer

# The channels a dispatch writes to.  The paused flags and the broker mailbox
# carry no vocabulary of their own — the VR player and the broker read them
# exactly as the desktop does — so they are drained here to be seen by the
# sweep rather than checked verb by verb.
_CHANNELS = (
    "main_player_cmd_file", "genau_cmd_file", "portrait_cmd_file", "landscape_cmd_file",
    "origenerator_cmd_file", "broker_cmd_file", "main_player_paused_file",
    "genau_paused_file", "audio_paused_file", "portrait_paused_file",
    "landscape_paused_file", "origenerator_paused_file",
)

# Window ops a VR session raises and nothing acts on: every role lives inside
# the one VR process with no HWND of its own, and the session hands its
# dispatch loop no window of the hosted Origenerator's either — that one boots
# parked and stays parked for the stay (docs/known-issues.md) — so these
# resolve nothing and settle into no-ops.  ``notice`` is here for a nearer
# reason: its overlay is a desktop window the session does not launch, so a
# flash that would confirm a key on the desktop confirms nothing in the
# headset.  Listed so the sweep can tell a designed no-op from a new one.
_OPS_WITH_NO_WINDOWS = frozenset({
    Op.NOTICE, Op.SHOW_ROLE, Op.HIDE_ROLE, Op.ACTIVATE_ROLE, Op.MINIMIZE_ROLE,
    Op.RESTORE_PARKED, Op.RESTACK_MAIN, Op.RESTACK_ORIGENERATOR,
    Op.DISABLE_ALL_TOPMOST, Op.RESTORE_ALL_TOPMOST,
})

# Ops that still act in a headset: the AHK bridge IS launched there, the clipper
# is a subprocess of its own, an RFB tab is skipped rather than misdelivered
# (a session with no browser window of its own opens none), and taking the
# players back from a hosted app is file work on the players' own channels, as
# is sending the hosted gallery to the clip Genau's role says it has locked.
_OPS_THAT_STILL_ACT = frozenset({
    Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS, Op.SAVE_CLIP, Op.OPEN_RFB_TAB,
    Op.TAKE_BACK_PLAYERS, Op.FOLLOW_GENAUS_LOCK,
})


def _headset_config(root: Path, *, names_an_origenerator: bool = True) -> BridgeConfig:
    """The bridge config a headset session runs on, built the way
    ``fun_time_vr.orchestrator.run_vr_bridge`` builds it: the VR manifest
    written out, read back, and handed to the dispatch loop's own builder.

    Off a fabricated config that names an Origenerator checkout, as a real one
    does, unless told not to.
    """
    folders = {name: root / name for name in (
        "primary", "portrait", "landscape", "vr", "weird", "clips", "audio", "state")}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    favs_file = root / "favs.csv"
    favs_file.write_text("local_file,web_url\n", encoding="utf-8")
    paths = {
        "ahk_exe": str(root / "AutoHotkey64.exe"),
        "python_exe": str(root / "python.exe"),
        "main_player_library_dirs": [str(folders["primary"])],
        "portrait_dirs": [str(folders["portrait"])],
        "landscape_dirs": [str(folders["landscape"])],
        "weird_dir": str(folders["weird"]),
        "clips_dir": str(folders["clips"]),
        "audio_dir": str(folders["audio"]),
        "favs_file": str(favs_file),
        "state_dir": str(folders["state"]),
    }
    if names_an_origenerator:
        checkout = root / "origenerator"
        checkout.mkdir(exist_ok=True)
        paths["origenerator_dir"] = str(checkout)
    config_file = root / "fun_time_config.json"
    config_file.write_text(json.dumps({
        "paths": paths,
        "layout": {"primary_monitor": 1, "secondary_monitor": 2,
                   "main_top_ratio": 0.7, "landscape_width_ratio": 0.6},
        "audio_companion": {"host": "127.0.0.1", "port": 50556},
        "vr": {"library_dirs": [str(folders["vr"])]},
    }), encoding="utf-8")
    manifest = write_manifest_data(build_vr_manifest(load_config(config_file)),
                                   folders["state"] / "windows_bridge_launch.ini")
    return build_bridge_config_from_manifest(LaunchManifest.read(manifest), vr_main_player=True)


def every_command() -> list[str]:
    """Every dispatch id the reference and the recognizer can produce.

    The reference is held to the AHK script and to ``VOICE_COMMANDS`` by
    ``tests/test_command_reference.py``, so walking it here walks every key and
    every phrase without this module parsing either.
    """
    commands = {
        command
        for section in build_reference_sections()
        for row in section.rows
        for command in row.commands
    }
    return sorted(commands | set(VOICE_COMMANDS.values()))


def _drain(config: BridgeConfig) -> dict[str, list[str]]:
    """What each channel was sent since the last drain, emptying them."""
    written: dict[str, list[str]] = {}
    for name in _CHANNELS:
        path = getattr(config, name, None)
        if path is None or not Path(path).exists():
            continue
        lines = [
            line.strip()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if lines:
            written[name] = lines
        Path(path).unlink()
    return written


def _sweep(config: BridgeConfig, satellites_modes) -> dict[str, dict[str, list[str]]]:
    """Dispatch every command in every pairing of the two slots' modes; report
    what each sent.

    Keyed ``"<main mode>/<satellites mode>/<command>"``, because several
    controls route by mode and the padlock this module was written for was dead
    in one and fine in the other — a failure has to say which.  The hosted app
    has always answered by the time a command is dispatched here, so switching
    into its mode is a switch rather than a notice that it is still starting.
    """
    _drain(config)
    sweep: dict[str, dict[str, list[str]]] = {}
    for main_mode in MAIN_MODES:
        for satellites_mode in satellites_modes:
            for command in every_command():
                state = BridgeState(main_mode=main_mode, satellites_mode=satellites_mode,
                                    origenerator_ready=True)
                _state, ops = dispatch_command(command, state, config, target_path="")
                written = _drain(config)
                written["__ops__"] = sorted({op.op for op in ops})
                sweep[f"{main_mode}/{satellites_mode}/{command}"] = written
    return sweep


@pytest.fixture(scope="module")
def landed(tmp_path_factory) -> dict[str, dict[str, list[str]]]:
    """The sweep, for a headset hosting the Origenerator its config names.

    Module-scoped: the sweep is the same for every assertion below and runs the
    whole reference four times.
    """
    return _sweep(_headset_config(tmp_path_factory.mktemp("vr_parity")),
                  (SATELLITE_VIDEO_MODE, ORIGENERATOR_MODE))


@pytest.fixture(scope="module")
def landed_hosting_none(tmp_path_factory) -> dict[str, dict[str, list[str]]]:
    """The sweep for a headset whose config names no Origenerator, in the
    origenerator mode a desktop session's shared state can still carry in."""
    root = tmp_path_factory.mktemp("vr_parity_hosting_none")
    return _sweep(_headset_config(root, names_an_origenerator=False), (ORIGENERATOR_MODE,))


def _sent_to(landed, channel: str) -> dict[str, str]:
    """Every verb sent on *channel* during the sweep, and one command that sent it."""
    seen: dict[str, str] = {}
    for where, written in landed.items():
        for line in written.get(channel, ()):
            seen.setdefault(line.split(None, 1)[0].upper(), where)
    return seen


def _main_role(tmp_path: Path) -> MainRole:
    """A real main role on fakes — the vocabulary asked of the method itself."""
    playlist = tmp_path / "main_player_playlist.tsv"
    videos = tmp_path / "videos"
    videos.mkdir(exist_ok=True)
    first, second = videos / "one.mp4", videos / "two.mp4"
    for video in (first, second):
        video.write_bytes(b"")
    script = videos / "one.funscript"
    script.write_text(json.dumps({"actions": [{"at": 0, "pos": 0}]}), encoding="utf-8")
    playlist.write_text(f"{first}\t{script}\n{second}\t\n", encoding="utf-8")
    return MainRole(
        player=FakePlayer(), driver=FakeDriver(), playlist_file=playlist,
        metadata_root=tmp_path / "metadata", vr_dirs=(),
    )


class TestTheMainPlayer:
    """The one role a VR session substitutes wholesale, and so the one that can
    quietly stop answering a key the desktop answers."""

    def test_every_verb_it_is_sent_is_one_it_answers(self, landed, tmp_path):
        """A key that posts a verb the VR main role drops is a control that does
        nothing at all in the headset, with nothing on screen to say so.

        ``'`` posted TOGGLE_LOCK into a role with no lock and the F key's
        SET_F_MODE went the same way: both dispatched cleanly, both dead, and
        the only trace was one "verb the VR main role does not handle" line in
        a log nobody reads while wearing a headset.
        """
        role = _main_role(tmp_path)
        dead: dict[str, str] = {}
        for verb, where in _sent_to(landed, "main_player_cmd_file").items():
            if verb in UNIMPLEMENTED_MAIN_PLAYER_VERBS:
                continue
            if not role.apply_command(_a_whole_line(verb), on_quit=lambda: None):
                dead[verb] = where
        assert not dead, (
            "the VR main role answers none of these and none is a stated "
            f"exception in UNIMPLEMENTED_MAIN_PLAYER_VERBS: {dead}"
        )

    def test_the_stated_exceptions_are_all_verbs_it_really_refuses(self, tmp_path):
        """The exception list may not carry a verb the role has since learned.

        Left there, a working control would read as a known gap for as long as
        anyone believed the list — which is the whole value of writing one.
        """
        role = _main_role(tmp_path)
        answered = [
            verb for verb in UNIMPLEMENTED_MAIN_PLAYER_VERBS
            if role.apply_command(_a_whole_line(verb), on_quit=lambda: None)
        ]
        assert not answered, (
            f"UNIMPLEMENTED_MAIN_PLAYER_VERBS names verbs the role now answers: {answered}"
        )

    def test_every_exception_says_why(self):
        """A bare list of dead verbs is a list nobody can act on later."""
        for verb, reason in UNIMPLEMENTED_MAIN_PLAYER_VERBS.items():
            assert reason.strip(), f"{verb} is excepted with no reason"

    def test_a_reset_unlocks_it_and_puts_its_speed_back_to_normal(self, tmp_path):
        config = _headset_config(tmp_path)
        role = _main_role(tmp_path)
        role.apply_command("SPEED_DOWN", on_quit=lambda: None)

        dispatch_command("main_reset", BridgeState(main_mode=MAIN_VIDEO_MODE), config)
        for line in config.main_player_cmd_file.read_text(encoding="utf-8").splitlines():
            role.apply_command(line, on_quit=lambda: None)

        assert (role.locked, role.speed) == (False, 1.0)


def _a_whole_line(verb: str) -> str:
    """*verb* with an argument where the spelling needs one.

    Half a command is refused by both roles on purpose, so a vocabulary check
    that sent bare verbs would report every value-taking one as unanswered.
    Which ones want a value is read off the two registries rather than listed
    here, so a verb that starts taking one cannot leave this check sending it bare.
    """
    return f"{verb} 1" if verb in _VERBS_THAT_TAKE_A_VALUE else verb


_VERBS_THAT_TAKE_A_VALUE = frozenset(
    spelling
    for registry in (main_player_controls.VERBS, roles.VERBS)
    for spelling, (_control, verb) in registry.items()
    if verb.takes_a_value
)


class TestTheSatellites:
    """Both satellites are the satellite package's own session, hosted offscreen
    — so their vocabulary is the desktop's, and this holds it there."""

    def test_every_verb_they_are_sent_is_one_the_session_answers(self, landed, tmp_path):
        videos = tmp_path / "sat"
        videos.mkdir()
        clips = [videos / "a.mp4", videos / "b.mp4"]
        for clip in clips:
            clip.write_bytes(b"")
        dead: dict[str, str] = {}
        for channel in ("portrait_cmd_file", "landscape_cmd_file"):
            for verb, where in _sent_to(landed, channel).items():
                session = SatelliteSession(list(clips), player=FakeSatellitePlayer())
                handled = apply_satellite_command(
                    _a_satellite_line(verb, clips[0]),
                    SatelliteControls(session, reload_playlist=lambda: None),
                )
                if not handled:
                    dead[verb] = where
        assert not dead, f"the hosted satellite session answers none of these: {dead}"

    def test_a_reset_unlocks_it_and_starts_a_fresh_browse_from_the_top(self, tmp_path):
        config = _headset_config(tmp_path)
        clips = [tmp_path / "portrait" / f"{name}.mp4" for name in ("alpha", "beta", "gamma")]
        for clip in clips:
            clip.write_bytes(b"")
        session = SatelliteSession(list(clips), player=FakeSatellitePlayer())
        session.set_locked(True)
        playlist = build_playlist_file_path(config.state_dir, PLAYLIST_PORTRAIT)

        dispatch_command(
            "portrait_reset", BridgeState(portrait=SatelliteState(locked=True, latest=True)), config)
        controls = SatelliteControls(
            session,
            reload_playlist=lambda: session.replace_playlist(
                [item.path for item in read_playlist(playlist)]),
        )
        for line in config.portrait_cmd_file.read_text(encoding="utf-8").splitlines():
            apply_satellite_command(line, controls)

        assert not session.is_locked
        assert session.current_video == read_playlist(playlist)[0].path


def _a_satellite_line(verb: str, clip: Path) -> str:
    return f"{verb} {clip}" if verb == "PLAY_FILE" else _a_whole_line(verb)


class TestGenau:
    """Genau's role is the same engine on the same registry, so its vocabulary
    is checked against that registry rather than against a built role."""

    def test_every_verb_it_is_sent_is_in_the_registry(self, landed):
        dead = {
            verb: where
            for verb, where in _sent_to(landed, "genau_cmd_file").items()
            if verb not in GENAU_VERBS
        }
        assert not dead, f"player_core's control registry answers none of these: {dead}"


# Said to a side in origenerator mode, where what a side is told is the hosted
# app's to answer, and answered by nothing over there.  Keyed by what the line
# says after the side.
_UNANSWERED_BY_THE_HOSTED_APP: dict[str, str] = {
    "wrong_action": "a show's picture carries no act label to strike, and the app "
                    "refuses the strike on purpose",
    "lock_action": "the app narrows a show to an act only when told the act's name",
    "lock_on": 'the spoken "lock" asks for a state, and the app answers only the '
               "toggle the key sends",
    "lock_off": 'the spoken "unlock" asks for a state, and the app answers only the '
                "toggle the key sends",
    "fmode_on": 'the spoken "f mode on" asks for a state, and the app answers only '
                "the toggle",
    "fmode_off": 'the spoken "f mode off" asks for a state, and the app answers only '
                 "the toggle",
}


def _said_after_the_side(line: str) -> str:
    for player in Player.SATELLITES:
        side = f"{player.label}_"
        if line.lower().startswith(side):
            return line[len(side):].lower()
    return line


class TestTheHostedOrigenerator:
    """In origenerator mode what is said to a side goes to the hosted app, which
    a headset hosts as the desktop does, and the app says in the document it
    publishes which lines its command file answers."""

    def test_it_is_sent_what_is_said_to_a_side_in_its_mode(self, landed):
        """The precondition, so the check below cannot pass on a channel the
        sweep never reached."""
        assert landed["video/origenerator/portrait_next"]["origenerator_cmd_file"] == [
            "portrait_next"]

    def test_every_line_it_is_sent_is_one_it_answers(self, landed):
        """A line the app does not answer is a control that does nothing at all
        in its mode, on the monitors and in the headset alike."""
        dead = {
            line: where
            for where, written in landed.items()
            for line in written.get("origenerator_cmd_file", ())
            if not answers(line)
            and _said_after_the_side(line) not in _UNANSWERED_BY_THE_HOSTED_APP
        }
        assert not dead, (
            "the hosted Origenerator answers none of these and none is a stated "
            f"exception in _UNANSWERED_BY_THE_HOSTED_APP: {dead}"
        )

    def test_the_stated_exceptions_are_all_lines_it_really_refuses(self):
        answered = [
            f"{player.label}_{said}"
            for said in _UNANSWERED_BY_THE_HOSTED_APP
            for player in Player.SATELLITES
            if answers(f"{player.label}_{said}")
        ]
        assert not answered, (
            f"_UNANSWERED_BY_THE_HOSTED_APP names lines the app now answers: {answered}"
        )

    def test_every_exception_says_why(self):
        for said, reason in _UNANSWERED_BY_THE_HOSTED_APP.items():
            assert reason.strip(), f"{said} is excepted with no reason"


class TestWhatAHeadsetDoesNotHost:
    """Windows, for any role; and an Origenerator, where its config names none."""

    def test_a_config_naming_no_origenerator_sends_one_nothing(self, landed_hosting_none):
        """Its manifest still names the app's command file, and the shared state
        a desktop session left can still say origenerator mode — so a gate on
        anything but the config would send every satellite verb to an app that
        is not there, and leave both satellites with no key that reached them.
        """
        routed = {
            where: written["origenerator_cmd_file"]
            for where, written in landed_hosting_none.items()
            if written.get("origenerator_cmd_file")
        }
        assert not routed, f"these reached an Origenerator the config names none of: {routed}"

    def test_every_window_op_raised_is_one_a_headset_has_an_answer_for(self, landed):
        """Either it acts here, or it is a known no-op because the roles have no
        windows.  A new op is neither until somebody says which."""
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

    def test_the_two_op_sets_between_them_cover_the_whole_vocabulary(self):
        """A new op added to the dispatcher must be placed on one side or the
        other, rather than waiting for a command that happens to raise it."""
        assert set(Op) == _OPS_WITH_NO_WINDOWS | _OPS_THAT_STILL_ACT
        assert not _OPS_WITH_NO_WINDOWS & _OPS_THAT_STILL_ACT
