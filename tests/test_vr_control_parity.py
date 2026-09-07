"""Every control the session can send, answered by something a VR session hosts.

A headset runs the same orchestrator, the same dispatch loop, the same AHK
hotkey script and the same voice control as the desktop.  What differs is who
is listening at the far end of each file channel: the main player is
:class:`fun_time_vr.roles.MainRole` rather than Nau, Genau and both satellites
live inside the one VR process, and the windows the desktop's ops act on do not
exist.  So a control can be perfectly routed and still be dead in the headset —
which is exactly how the main-slot padlock and F-mode's status line came to be
dead there with the whole suite green.

This module closes that.  It walks every command the reference can produce —
every hotkey, every spoken phrase — through the real dispatch against a
VR-shaped config, and holds each verb that lands to the vocabulary of whatever
will actually read it in a VR session.  The only way to leave a control dead in
the headset is to name it, with its reason, in
:data:`fun_time_vr.roles.UNIMPLEMENTED_NAU_VERBS` or in one of the sets here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from player_core.genau_controls import VERBS as GENAU_VERBS

from fun_time.bridge_records import BridgeConfig, Op
from fun_time.command_dispatch import dispatch_command
from fun_time.command_reference import build_reference_sections
from fun_time.mode_plan import VIDEO_MODE
from fun_time.shared_state import BridgeState
from fun_time.voice_commands import VOICE_COMMANDS
from fun_time_vr.roles import UNIMPLEMENTED_NAU_VERBS, MainRole
from satellite.runtime import apply_command as apply_satellite_command
from satellite.session import SatelliteSession
from tests.satellite_fakes import FakeSatellitePlayer
from tests.test_vr_roles import FakeDriver, FakePlayer

# The channels a dispatch writes to.  The paused flags and the broker mailbox
# carry no vocabulary of their own — the VR player and the broker read them
# exactly as the desktop does — so they are drained here to be seen by the
# sweep rather than checked verb by verb.
_CHANNELS = (
    "nau_cmd_file", "genau_cmd_file", "portrait_cmd_file", "landscape_cmd_file",
    "origenerator_cmd_file", "broker_cmd_file", "nau_paused_file",
    "genau_paused_file", "audio_paused_file", "portrait_paused_file",
    "landscape_paused_file",
)

# Window ops a VR session raises and nothing acts on: every role lives inside
# the one VR process with no HWND of its own, so these resolve nothing and
# settle into no-ops — the state of affairs fun_time_vr.orchestrator's docstring
# names.  ``notice`` is here for a nearer reason: its overlay is a desktop
# window the session does not launch, so a flash that would confirm a key on the
# desktop confirms nothing in the headset.  Listed so the sweep can tell a
# designed no-op from a new one.
_OPS_WITH_NO_WINDOWS = frozenset({
    Op.NOTICE, Op.SHOW_ROLE, Op.HIDE_ROLE, Op.ACTIVATE_ROLE, Op.MINIMIZE_ROLE,
    Op.RESTORE_PARKED, Op.RESTACK_MAIN, Op.RESTACK_SATELLITES,
    Op.DISABLE_ALL_TOPMOST, Op.RESTORE_ALL_TOPMOST,
})

# Ops that still act in a headset: the AHK bridge IS launched there, the clipper
# is a subprocess of its own, and an RFB tab is skipped rather than misdelivered
# (a session with no browser window of its own opens none).
_OPS_THAT_STILL_ACT = frozenset({
    Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS, Op.SAVE_CLIP, Op.OPEN_RFB_TAB,
})

# The other main-slot mode.  ``mode_plan`` names only the video one, since
# genau is simply "not video" everywhere it is asked; the sweep needs the word.
_GENAU_MODE = "genau"


def _vr_config(tmp_path: Path) -> BridgeConfig:
    """A bridge config shaped the way ``fun_time_vr.orchestrator`` builds one.

    ``origenerator_enabled`` is False for the reason the builder gives: the
    hosted app rides in a Chrome window a VR session never opens.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    favs_file = tmp_path / "favs.csv"
    favs_file.write_text("local_file,web_url\n", encoding="utf-8")
    weird_dir = tmp_path / "weird"
    weird_dir.mkdir(exist_ok=True)
    for name in ("primary", "portrait", "landscape"):
        (tmp_path / name).mkdir(exist_ok=True)
    return BridgeConfig(
        vr_main_player=True,
        origenerator_enabled=False,
        origenerator_cmd_file=state_dir / "origenerator_cmd.txt",
        portrait_cmd_file=state_dir / "portrait_cmd.txt",
        portrait_paused_file=state_dir / "portrait_paused.txt",
        portrait_status_file=state_dir / "portrait_status.txt",
        portrait_playlist_file=state_dir / "portrait_playlist.tsv",
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        landscape_playlist_file=state_dir / "landscape_playlist.tsv",
        favs_file=favs_file,
        weird_dir=weird_dir,
        state_dir=state_dir,
        main_sources=str(tmp_path / "primary"),
        portrait_sources=str(tmp_path / "portrait"),
        landscape_sources=str(tmp_path / "landscape"),
        genau_mode_file=state_dir / "genau_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        audio_volume_file=state_dir / "audio_volume.txt",
        nau_cmd_file=state_dir / "nau_cmd.txt",
        nau_paused_file=state_dir / "nau_paused.txt",
        nau_status_file=state_dir / "nau_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
        broker_cmd_file=state_dir / "broker_cmd.txt",
    )


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


@pytest.fixture(scope="module")
def landed(tmp_path_factory) -> dict[str, dict[str, list[str]]]:
    """Dispatch every command in both main-slot modes; report what each sent.

    Keyed ``"<mode>/<command>"``, because several controls route by mode and
    the padlock this module was written for was dead in one and fine in the
    other — a failure has to say which.  Module-scoped: the sweep is the same
    for every assertion below and runs the whole reference twice.
    """
    config = _vr_config(tmp_path_factory.mktemp("vr_parity"))
    _drain(config)
    sweep: dict[str, dict[str, list[str]]] = {}
    for mode in (VIDEO_MODE, _GENAU_MODE):
        for command in every_command():
            state = BridgeState(main_mode=mode, satellites_mode=VIDEO_MODE)
            _state, ops = dispatch_command(command, state, config, target_path="")
            written = _drain(config)
            written["__ops__"] = sorted({op.op for op in ops})
            sweep[f"{mode}/{command}"] = written
    return sweep


def _sent_to(landed, channel: str) -> dict[str, str]:
    """Every verb sent on *channel* during the sweep, and one command that sent it."""
    seen: dict[str, str] = {}
    for where, written in landed.items():
        for line in written.get(channel, ()):
            seen.setdefault(line.split(None, 1)[0].upper(), where)
    return seen


def _main_role(tmp_path: Path) -> MainRole:
    """A real main role on fakes — the vocabulary asked of the method itself."""
    playlist = tmp_path / "nau_playlist.tsv"
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
        for verb, where in _sent_to(landed, "nau_cmd_file").items():
            if verb in UNIMPLEMENTED_NAU_VERBS:
                continue
            if not role.apply_command(_a_whole_line(verb), on_quit=lambda: None):
                dead[verb] = where
        assert not dead, (
            "the VR main role answers none of these and none is a stated "
            f"exception in UNIMPLEMENTED_NAU_VERBS: {dead}"
        )

    def test_the_stated_exceptions_are_all_verbs_it_really_refuses(self, tmp_path):
        """The exception list may not carry a verb the role has since learned.

        Left there, a working control would read as a known gap for as long as
        anyone believed the list — which is the whole value of writing one.
        """
        role = _main_role(tmp_path)
        answered = [
            verb for verb in UNIMPLEMENTED_NAU_VERBS
            if role.apply_command(_a_whole_line(verb), on_quit=lambda: None)
        ]
        assert not answered, (
            f"UNIMPLEMENTED_NAU_VERBS names verbs the role now answers: {answered}"
        )

    def test_every_exception_says_why(self):
        """A bare list of dead verbs is a list nobody can act on later."""
        for verb, reason in UNIMPLEMENTED_NAU_VERBS.items():
            assert reason.strip(), f"{verb} is excepted with no reason"


def _a_whole_line(verb: str) -> str:
    """*verb* with an argument where the spelling needs one.

    Half a command is refused by both roles on purpose, so a vocabulary check
    that sent bare verbs would report every value-taking one as unanswered.
    """
    return f"{verb} 1" if verb in _VERBS_THAT_TAKE_A_VALUE else verb


_VERBS_THAT_TAKE_A_VALUE = frozenset({
    "SET_SPEED", "SET_VOLUME", "SET_TCODE_ENABLED", "SET_F_MODE", "SET_LENGTH_MODE",
    "SET_LOOP", "PLAY_FILE",
})


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
                    _a_satellite_line(verb, clips[0]), session,
                    stop_event=None, reload_playlist=lambda: None,
                )
                if not handled:
                    dead[verb] = where
        assert not dead, f"the hosted satellite session answers none of these: {dead}"


def _a_satellite_line(verb: str, clip: Path) -> str:
    return f"{verb} {clip}" if verb == "PLAY_FILE" else verb


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


class TestWhatAHeadsetDoesNotHost:
    def test_nothing_is_ever_routed_to_an_origenerator(self, landed):
        """A VR session launches no Random Favs Browser, and the hosted app
        rides in its Chrome window — so a satellite verb sent there is a key
        that vanishes.

        Worse than vanishing: origenerator mode pauses both satellite PLAYERS
        for the whole mode, so a headset that merely resumed the mode from a
        desktop session sat in front of two black screens with no key that
        reached them.  ``origenerator_enabled`` off is what settles it, and the
        dispatch loop then carries a resumed mode back to video.
        """
        routed = {
            where: written["origenerator_cmd_file"]
            for where, written in landed.items()
            if written.get("origenerator_cmd_file")
        }
        assert not routed, f"these reached an Origenerator the headset has none of: {routed}"

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
