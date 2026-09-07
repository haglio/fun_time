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
_.paintEvent  # Qt event override
_.mouseMoveEvent  # Qt event override
_.optionxform  # ConfigParser hook, set to keep key case

# --- Win32 struct fields written for an API call, never read back ---
_.cbSize
dwSize  # PROCESSENTRY32, for Toolhelp32

# --- Read from a sibling package, which is a scan of its own ---
tcode_udp_host  # fun_time_vr/orchestrator.py
tcode_udp_port  # fun_time_vr/orchestrator.py
compositor_layers  # fun_time_vr/orchestrator.py

# --- Read from outside vulture's scan ---
is_process_alive  # tests and the integration reap
get_process_image_name  # the integration reap, to tell a leftover app from pytest
_.active_filter  # HudClicks lives in player_core; the reads are in that sibling

PROJECT_VR_ICON  # project_paths; read by fun_time_vr, a scan of its own
set_window_icon  # win32; the VR session's window asks for it, a scan of its own
draw_nothing_at_all  # win32; the VR session's window asks for it too
_.current_funscript  # MainRole; player_core's drive gate reads it off the role
# The headset hold's channel: written by fun_time_vr's orchestrator and read by
# its player, each a scan of its own.
hold_the_headset
headset_hold_asked
headset_hold_stops_the_runtime
report_the_headset_held
headset_is_held
say_the_crossing_is_cancelled  # session_handoff; the VR orchestrator's cancel says it
