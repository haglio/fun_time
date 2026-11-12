"""Vulture whitelist: false positives that are not dead code.

Each entry tells vulture the name is used, suppressing the report.  Vulture
matches by bare name, so an entry that suppresses nothing keeps covering
whatever is given that name next: the family's dead-code gate asserts every entry
here still answers a report, and an entry may only be added with the reason it
answers one.
"""
from __future__ import annotations

# --- Called by a framework, not by us ---
_.do_GET  # http.server dispatches by getattr
_.set_showing  # the Funestra (funestra) tells each User whether it has the window
_.own_frame  # the Funestra asks the User in front for the frame it brings
_.hide  # the Funestra takes a User's own frame down off the headset's surface
_.pick  # the Funestra hands them the projection picked off the list
_.set_bar  # the Funestra hands them the angle the Angle bar is set to
_.leaveEvent  # Qt calls it when the pointer leaves the dashboard bar
_.decorationPosition  # Qt's item view reads it off the style option, to put a letter's mark after it
_.ERROR_CHECKING  # PyOpenGL reads it once, when OpenGL.GL is first imported
_.optionxform  # ConfigParser hook, set to keep key case
_.resizable  # pygame's Window property, set so SDL takes a resize from outside

# --- Win32 struct fields written for an API call, never read back ---
_.cbSize
dwSize  # PROCESSENTRY32, for Toolhelp32

# --- Read from a sibling package, which is a scan of its own ---
build_genau_engine  # main_funestra.genau builds Genau's engine with it
tell_origenerator_it_went  # main_funestra.genau hands Genau's engine it
_._folders  # main_funestra.genau's Genau asks the headset's own for its flicks folders
_.current_flick  # main_funestra.genau, off the engine
_.playhead  # main_funestra.genau, off the engine
_.time_on_screen  # main_funestra.genau, off the engine
_.manifest_fields  # fun_time_vr.orchestrator carries Genau's settings into the manifest
from_channel  # satellite.contract; fun_time_vr's Funestra and the sequencer
from_manifest  # genau_config and main_funestra.contract; fun_time_vr's Funestras
top_block  # main_funestra.kino; funestra's Funestra heads its HUD with it
_._shaped  # main_funestra.genau's Genau narrows its flicks through the headset's own
# fun_time.arrivals asks each of these of main_funestra.genau's flicks before it
# hands them what arrived.
_.in_latest
_.plays_its_own_list
_.lets_through
_.put_up_next
to_argv  # satellite.contract; the session's satellite launcher
compositor_layers  # fun_time_vr/orchestrator.py
process_running  # win32_process; fun_time_vr's shutdown asks it whether the VR runtime is up
GalleryFlips  # main_funestra.genau hands it to Genau's engine as where an AI flick's flip is kept
_.keeps  # funestra's FlickFlip asks GalleryFlips whether it keeps a flick's flip
_.is_flipped  # funestra's FlickFlip asks GalleryFlips whether a flick it keeps is flipped

# --- Read from outside vulture's scan ---
settle  # MainListBuilds; test_vr_control_parity drains a dispatch before reading it
get_process_image_name  # the integration reap, to tell a leftover app from pytest

PROJECT_VR_ICON  # project_paths; read by fun_time_vr, a scan of its own
set_the_icon_alt_tab_draws  # win32; the VR session's window asks for it, a scan of its own
draw_nothing_at_all  # win32; the VR session's window asks for it too
# The headset hold's channel: written by fun_time_vr's orchestrator and read by
# its Funestra, each a scan of its own.
hold_the_headset
headset_hold_asked
headset_hold_stops_the_runtime
report_the_headset_held
headset_is_held
CANCEL_OPENING_FUN_TIME_VR  # overlay_progress; the VR orchestrator's launch loading screen says it
CANCEL_CLOSING_FUN_TIME_VR  # overlay_progress; the VR orchestrator's closing screen says it

# shared_ui.PressableDrawing hands the pointer leaving to what it was given (fun_time.rfb_slot_strip.RfbSlotStrip).
leave
