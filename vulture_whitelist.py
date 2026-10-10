"""Vulture whitelist: false positives that are not dead code.

Each entry tells vulture the name is used, suppressing the report.  Vulture
matches by bare name, so an entry that suppresses nothing keeps covering
whatever is given that name next: tests/test_dead_code.py asserts every entry
here still answers a report, and an entry may only be added with the reason it
answers one.
"""
from __future__ import annotations

# --- Called by a framework, not by us ---
_.do_GET  # http.server dispatches by getattr
_.set_showing  # the Funestra (player_core) tells each User whether it has the window
_.picture  # the Funestra asks the User in front for the picture it brings
_.leaveEvent  # Qt calls it when the pointer leaves the dashboard bar
_.mouseMoveEvent  # Qt event override
_.ERROR_CHECKING  # PyOpenGL reads it once, when OpenGL.GL is first imported
_.optionxform  # ConfigParser hook, set to keep key case
_.resizable  # pygame's Window property, set so SDL takes a resize from outside

# --- Win32 struct fields written for an API call, never read back ---
_.cbSize
dwSize  # PROCESSENTRY32, for Toolhelp32
_.LimitFlags  # JOBOBJECT_BASIC_LIMIT_INFORMATION, for a job's limits
_.PriorityClass  # JOBOBJECT_BASIC_LIMIT_INFORMATION, for the integration run's job

# CommandFiles reaches these by side, through player_file: a satellite's
# channels are read as a group now (satellite.contract), so no module
# spells either key.
portrait_hud_file
landscape_hud_file

# --- Read from a sibling package, which is a scan of its own ---
build_genau_engine  # main_player.genau builds Genau's engine with it
run_ahead_of_background_work  # main_player.app, before its window opens
_.current_clip  # main_player.genau, off the engine
_.playhead  # main_player.genau, off the engine
_.manifest_fields  # fun_time_vr.orchestrator carries Genau's settings into the manifest
from_manifest  # satellite.contract; fun_time_vr's player and the sequencer
top_block  # main_player.kino; player_core's Funestra heads its console with it
to_argv  # satellite.contract; the session's satellite launcher
tcode_udp_host  # fun_time_vr/orchestrator.py
tcode_udp_port  # fun_time_vr/orchestrator.py
compositor_layers  # fun_time_vr/orchestrator.py

# --- Read from outside vulture's scan ---
settle  # MainListBuilds; test_vr_control_parity drains a dispatch before reading it
get_process_image_name  # the integration reap, to tell a leftover app from pytest

PROJECT_VR_ICON  # project_paths; read by fun_time_vr, a scan of its own
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
