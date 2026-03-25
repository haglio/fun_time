# Known Issues

## Robot Hand Disable / Re-enable Reliability

- Status: Deferred
- Symptom: Toggling Robot Hand with `r` can behave inconsistently when disabling takeover and then re-enabling it later.
- Notes:
  - This behavior predates the current extraction work.
  - Robot Hand transition planning has been extracted into Python, which should make this easier to fix later without adding more controller-side complexity.

## OmniPause Does Not Fully Drop Fun Time Windows From Topmost

- Status: Deferred
- Symptom: Pressing `Esc` enters OmniPause, but Fun Time-managed windows can still remain effectively on top of other windows.
- Scope:
  - Primary VLC
  - Satellite VLCs
  - MFP
  - Fun Time overlay/dashboard
  - Robot Hand
- Notes:
  - Several controller-side attempts were made to force topmost off during OmniPause.
  - Those attempts did not resolve the issue reliably enough to justify carrying more AHK-specific complexity while the Windows bridge is actively being reduced toward a thinner hotkey/window listener.
  - This should be revisited after more window-management responsibility has been extracted out of `windows_bridge.ahk`.

## Python Dashboard Text Rendering Is Ugly

- Status: Deferred
- Symptom: After moving the Fun Time dashboard rendering from AHK to the Python/Tk dashboard app, the overlay appears in the correct place and is functionally usable, but the text rendering/layout looks noticeably worse than the old AHK version.
- Notes:
  - This is currently treated as visual polish debt, not a blocker for the extraction effort.
  - The immediate architectural goal is to keep moving dashboard and runtime responsibilities out of `windows_bridge.ahk`.
  - Future follow-up should improve typography/text wrapping/rendering in the Python dashboard without moving the UI back into AHK.
