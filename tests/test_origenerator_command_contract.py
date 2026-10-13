"""Every line this session writes on the hosted Origenerator's command file,
held to what the app says it answers.

What a key or a phrase says to a side in the app's mode is walked, every one of
them, by ``tests/test_vr_control_parity.py``.  These are the rest: a Satellite
Funestra's own mode buttons, the console's enhanced-only switch, the room's
F-Mode reaching each show's own, the gallery following a lock Genau took, and a
copy of the app the session took over being handed back.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fun_time.command_dispatch import dispatch_command
from fun_time.config import load_config
from fun_time.gallery_follows_genau import GalleryFollowsGenau
from fun_time.manifest import LaunchManifest, write_windows_bridge_manifest
from fun_time.origenerator_switches import ORIGENERATOR_MODE
from fun_time.room_funestras import RoomFunestra
from fun_time.shared_state import BridgeState, SatelliteState
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time.windows_bridge_orchestrator import see_the_hosted_app_out
from tests.hosted_shows import publish_the_show
from tests.origenerator_contract import answers


def _what_it_was_sent(channel: Path) -> list[str]:
    return channel.read_text(encoding="utf-8").splitlines()


def _hosting_bridge(cfg_factory, tmp_path):
    checkout = tmp_path / "origenerator"
    checkout.mkdir()
    config = load_config(cfg_factory({"paths": {"origenerator_dir": str(checkout)}}))
    return build_bridge_config_from_manifest(
        LaunchManifest.read(write_windows_bridge_manifest(config, tmp_path / "manifest.ini")))


def _both_hosted() -> BridgeState:
    hosted = SatelliteState(mode=ORIGENERATOR_MODE)
    return BridgeState(portrait=hosted, landscape=hosted, origenerator_ready=True)


def test_each_satellites_own_mode_buttons(cfg_factory, tmp_path):
    bridge = _hosting_bridge(cfg_factory, tmp_path)
    state = BridgeState(origenerator_ready=True)

    for funestra in RoomFunestra.SATELLITES:
        for mode in ("origenerator", "kino"):
            state, _ops = dispatch_command(f"{funestra.label}_{mode}_activate", state, bridge)

    sent = _what_it_was_sent(bridge.origenerator_cmd_file)
    assert len(sent) == 4 and all(map(answers, sent)), sent


def test_the_consoles_enhanced_only_switch(cfg_factory, tmp_path):
    bridge = _hosting_bridge(cfg_factory, tmp_path)

    dispatch_command("genau_filter_enhanced", _both_hosted(), bridge)

    sent = _what_it_was_sent(bridge.origenerator_cmd_file)
    assert sent and all(map(answers, sent)), sent


def test_the_rooms_f_mode_reaching_each_hosted_show(cfg_factory, tmp_path):
    bridge = _hosting_bridge(cfg_factory, tmp_path)
    for funestra in RoomFunestra.SATELLITES:
        publish_the_show(bridge, funestra, f_mode=False)

    with patch("fun_time.command_dispatch.apply_fmode") as rebuild:
        rebuild.return_value = SimpleNamespace(funestras=(), log_message="")
        dispatch_command("fmode_toggle", _both_hosted(), bridge)

    sent = _what_it_was_sent(bridge.origenerator_cmd_file)
    assert sent and all(map(answers, sent)), sent


def test_the_gallery_following_a_lock_genau_took(tmp_path):
    status, channel = tmp_path / "genau_status.txt", tmp_path / "origenerator_cmd.txt"
    status.write_text(f"locked=1\nflick={tmp_path / 'flicks' / 'scene one.mp4'}\n",
                      encoding="utf-8")
    follow = GalleryFollowsGenau(genau_status_file=status, origenerator_cmd_file=channel)

    follow.expect_a_lock()
    follow.sync()

    sent = _what_it_was_sent(channel)
    assert sent and all(map(answers, sent)), sent


def test_handing_back_a_copy_the_session_took_over(tmp_path):
    channel = tmp_path / "origenerator_cmd.txt"

    see_the_hosted_app_out(tmp_path, None, channel, keep=False, taken_over=True)

    sent = _what_it_was_sent(channel)
    assert sent and all(map(answers, sent)), sent
