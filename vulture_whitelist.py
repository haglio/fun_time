"""Vulture whitelist — false positives that are not dead code.

Each entry tells vulture the name is used, suppressing the report.  Vulture
matches by bare name, so an entry that suppresses nothing keeps covering
whatever is given that name next: tests/test_dead_code.py asserts every entry
here still answers a report, and an entry may only be added with the reason it
answers one.
"""

# --- Called by a framework, not by us ---
_.do_GET  # http.server dispatches by getattr
_.paintEvent  # Qt event override
_.mousePressEvent  # Qt event override
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
_read_shortcut_app_user_model_id  # tests only
_.active_filter  # HudClicks lives in player_core; the reads are in that sibling

# Read by fun_time_vr, which vulture is not pointed at.
project_paths.PROJECT_VR_ICON
