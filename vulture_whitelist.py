"""Vulture whitelist â€” false positives that are not dead code.

Each entry tells vulture the name is used, suppressing the report.  Vulture
matches by bare name, so an entry that suppresses nothing keeps covering
whatever is given that name next: tests/test_dead_code.py asserts every entry
here still answers a report, and an entry may only be added with the reason it
answers one.
"""
from __future__ import annotations

# --- Called by a framework, not by us ---
_.do_GET  # http.server dispatches by getattr
_.leaveEvent  # Qt calls it when the pointer leaves the dashboard bar
_.mouseMoveEvent  # Qt event override
_.optionxform  # ConfigParser hook, set to keep key case

# --- Win32 struct fields written for an API call, never read back ---
_.cbSize
dwSize  # PROCESSENTRY32, for Toolhelp32

# CommandFiles reaches these by side, through player_file: a satellite's
# channels are read as a group now (satellite.contract), so no module
# spells either key.
portrait_hud_file
landscape_hud_file

# --- Read from a sibling package, which is a scan of its own ---
from_manifest  # satellite.contract; fun_time_vr's player and the sequencer
to_argv  # satellite.contract; the session's satellite launcher
tcode_udp_host  # fun_time_vr/orchestrator.py
tcode_udp_port  # fun_time_vr/orchestrator.py
compositor_layers  # fun_time_vr/orchestrator.py

# --- Read from outside vulture's scan ---
is_process_alive  # tests and the integration reap
get_process_image_name  # the integration reap, to tell a leftover app from pytest
_.active_filter  # HudClicks lives in player_core; the reads are in that sibling

PROJECT_VR_ICON  # project_paths; read by fun_time_vr, a scan of its own
timeline_x  # main_player.overlay; the headset's scrubber repaints on it, a scan of its own
set_window_icon  # win32; the VR session's window asks for it, a scan of its own
draw_nothing_at_all  # win32; the VR session's window asks for it too
# The headset hold's channel: written by fun_time_vr's orchestrator and read by
# its player, each a scan of its own.
hold_the_headset
headset_hold_asked
headset_hold_stops_the_runtime
report_the_headset_held
headset_is_held
CANCEL_OPENING_FUN_TIME_VR  # overlay_progress; the VR orchestrator's launch cover says it
CANCEL_CLOSING_FUN_TIME_VR  # overlay_progress; the VR orchestrator's closing cover says it
