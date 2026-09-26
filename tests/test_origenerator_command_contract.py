"""Every line this session writes on the hosted Origenerator's command file,
held to what the app says it answers.

What a key or a phrase says to a side in the app's mode is walked, every one of
them, by ``tests/test_vr_control_parity.py``.  These are the rest: the console's
enhanced-only switch, the gallery following a lock Genau took, and a copy of
the app the session took over being handed back.
"""
from __future__ import annotations

from pathlib import Path

from fun_time.command_dispatch import dispatch_command
from fun_time.config import load_config
from fun_time.gallery_follows_genau import GalleryFollowsGenau
from fun_time.manifest import LaunchManifest, write_windows_bridge_manifest
from fun_time.satellites_mode import ORIGENERATOR_MODE
from fun_time.shared_state import BridgeState
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time.windows_bridge_orchestrator import see_the_hosted_app_out
from tests.origenerator_contract import answers


def _what_it_was_sent(channel: Path) -> list[str]:
    return channel.read_text(encoding="utf-8").splitlines()


def test_the_consoles_enhanced_only_switch(cfg_factory, tmp_path):
    checkout = tmp_path / "origenerator"
    checkout.mkdir()
    config = load_config(cfg_factory({"paths": {"origenerator_dir": str(checkout)}}))
    bridge = build_bridge_config_from_manifest(
        LaunchManifest.read(write_windows_bridge_manifest(config, tmp_path / "manifest.ini")))

    dispatch_command("genau_filter_enhanced", BridgeState(
        satellites_mode=ORIGENERATOR_MODE, origenerator_ready=True), bridge)

    sent = _what_it_was_sent(bridge.origenerator_cmd_file)
    assert sent and all(map(answers, sent)), sent


def test_the_gallery_following_a_lock_genau_took(tmp_path):
    status, channel = tmp_path / "genau_status.txt", tmp_path / "origenerator_cmd.txt"
    status.write_text(f"locked=1\nclip={tmp_path / 'clips' / 'scene one.mp4'}\n",
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
