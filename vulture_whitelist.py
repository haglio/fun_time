"""Vulture whitelist — false positives that are not dead code.

Each entry tells vulture the name is used, suppressing the report.
Grouped by reason so reviewers can verify each entry belongs here.
"""

# --- http.server handler dispatch (called by the framework via getattr) ---
_.do_GET  # noqa

# --- Qt event overrides (called by the framework, not by our code) ---
_.paintEvent  # noqa
_.mousePressEvent  # noqa
_.mouseMoveEvent  # noqa
_.closeEvent  # noqa

# --- Win32 struct fields (must be set for API calls to work) ---
_.cbSize  # noqa
_.dwFlags  # noqa

# --- ConfigParser case-sensitivity (optionxform = str) ---
_.optionxform  # noqa

# --- sounddevice callback signature (all params required by library) ---
frames  # noqa
time_info  # noqa

# --- Dataclass fields (accessed through instances; vulture can't trace) ---
# GenauConfig
shuffle_on_load  # noqa
beats_per_loop  # noqa
clip_cache_size  # noqa
render_batch  # noqa
bpm_smoothing  # noqa
sync_strength  # noqa
notify_host  # noqa
notify_port  # noqa
status_hide_ms  # noqa
resize_debounce_ms  # noqa
# FModePlaylistPlan / FModeFlowResult
success  # noqa
primary_count  # noqa
portrait_count  # noqa
landscape_count  # noqa
# StartupResult
layout_plan  # noqa
core_hwnds  # noqa

# --- Functions vulture reports as unused at low confidence (false positive) ---
# build_mode_switch_plan is called from runtime_flow.py
build_mode_switch_plan  # noqa

# --- Dashboard layout fields accessed through instances (vulture can't trace) ---
hybrid_mode_button  # noqa
nau_mode_button  # noqa
hybrid_quarter_button  # noqa
hybrid_open_file_dialog  # noqa
hybrid_genau_amp_label  # noqa
hybrid_genau_amp_up  # noqa
hybrid_genau_amp_down  # noqa
hybrid_genau_ctr_label  # noqa
hybrid_genau_ctr_up  # noqa
hybrid_genau_ctr_down  # noqa
hybrid_genau_spd_label  # noqa
hybrid_genau_spd_up  # noqa
hybrid_genau_spd_down  # noqa

# --- Constants / functions used only from tests or integration tests ---
# (vulture scans production code only; test imports are invisible to it)
COLOR_YELLOW  # noqa
MUTEX_BROKER  # noqa
is_process_alive  # noqa
# The integration reap uses this to tell a leftover app process from the pytest
# that must survive; production kills children by recorded creation time instead.
get_process_image_name  # noqa
# The live-session claim's reader.  Production only ever writes the claim; the
# thing that has to read it is the integration guard, which lives under tests/.
# Both halves stay in fun_time.live_session so the file format has one home.
live_session_state_dir  # noqa
is_window_minimized  # noqa
_read_shortcut_app_user_model_id  # noqa
# reset_group_index_cache is test isolation support for the module-level
# group-index cache.
reset_group_index_cache  # noqa
