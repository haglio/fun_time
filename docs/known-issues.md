# Known Issues

## Genau Disable / Re-enable Reliability

- Status: Deferred
- Symptom: Toggling Genau with `r` can behave inconsistently when disabling takeover and then re-enabling it later.
- Notes:
  - This behavior predates the current extraction work.
  - Genau transition planning has been extracted into Python, which should make this easier to fix later without adding more controller-side complexity.

## OmniPause Does Not Fully Drop Fun Time Windows From Topmost

- Status: Resolved (2026-07-05)
- Symptom: Pressing `Esc` enters OmniPause, but Fun Time-managed windows can still remain effectively on top of other windows.
- Scope:
  - Primary VLC
  - Satellite VLCs
  - MFP
  - Fun Time overlay/dashboard
  - Genau
- Notes:
  - Several controller-side attempts were made to force topmost off during OmniPause.
  - Those attempts did not resolve the issue reliably enough to justify carrying more AHK-specific complexity while the Windows bridge is actively being reduced toward a thinner hotkey/window listener.
  - This should be revisited after more window-management responsibility has been extracted out of `windows_bridge.ahk`.
- Resolution: Window management now lives entirely in the Python bridge, whose OmniPause pass (`_sink_all_windows`) pushes every managed window to the bottom of the z-order. The last window that stayed pinned was Nau: startup blanket-promoted every window to topmost, but OmniPause consulted a per-role policy where Nau was recorded as non-topmost, so the pass skipped it and never released it. Both sides now read one shared `role_topmost` policy (`fun_time/window_roles.py`), so startup and OmniPause can no longer disagree. The pass also had to *sink* rather than merely un-topmost: `HWND_NOTOPMOST` places a window above every non-topmost window, so clearing the flag parked the player straight on top of whatever the user switched to — `HWND_BOTTOM` (see `sink_below_all_windows`) is what actually frees the desktop. (MFP no longer exists.)

## Python Dashboard Text Rendering Is Ugly

- Status: Deferred
- Symptom: After moving the Fun Time dashboard rendering from AHK to the Python/Tk dashboard app, the overlay appears in the correct place and is functionally usable, but the text rendering/layout looks noticeably worse than the old AHK version.
- Notes:
  - This is currently treated as visual polish debt, not a blocker for the extraction effort.
  - The immediate architectural goal is to keep moving dashboard and runtime responsibilities out of `windows_bridge.ahk`.
  - Future follow-up should improve typography/text wrapping/rendering in the Python dashboard without moving the UI back into AHK.
