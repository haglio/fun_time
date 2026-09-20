# Known Issues

## FunTimeVR: The First Seconds Of A Launch Are Not Covered

- Status: Deferred (2026-09-07)
- Symptom: Between double-clicking Fun Time VR and the cover appearing, the
  headset shows the VR runtime's own environment — its home or void, however
  long the runtime takes to answer (a cold PimaxXR auto-start can be 45s).
- Scope:
  - Only that stretch. From the cover's first frame onward the loading cover
    hides the whole of the room assembling, and the closing cover hides the
    whole of the teardown.
- Notes:
  - It cannot be covered from here. An OpenXR app owns no compositor layer
    until it has a session, so before `xrBeginSession` there is nothing this
    process can submit and nothing it can draw on; whatever the headset shows
    then is the runtime's, not ours. The desktop has the same gap for the same
    reason — the moment between `launch.vbs` and the cover's first paint shows
    the user's desktop — it is just much shorter there, because starting a
    tkinter window is not starting a VR runtime.
  - What IS ours in that stretch is already handled: the cover goes up before
    the players are built rather than after (the compositor holds that frame
    through the seconds of mpv bring-up), and its progress file is written
    before the player is launched, so the bar is where the launch actually is
    from the very first frame the headset can show.

## FunTimeVR: What a VR Session Does Not Launch

- Status: Deferred
- Scope:
  - Not launched in VR: the Qt dashboard and its log panel, the Random Favs Browser,
    and the loopback server. The main slot's two modes,
    Genau's clips and the Robot Hand's stretches, and the audio companion (on the
    headset's output) all run in VR as of 2026-09-04, on the engine that moved to
    `player_core` for it; GenauVR, the standalone headset app, is retired with that.
  - The hosted Origenerator IS launched, as of 2026-09-20, so origenerator mode
    runs in the headset: the mode's shows are the satellite players' own
    playlists (`fun_time.player_handover`), and the headset's satellites are
    players. `fun_time.hosted_origenerator` brings the app up for either shape
    of session and sees it out of either teardown. What a headset has nowhere to
    show is the app's own window, which shares the Random Favs Browser's rect:
    it boots parked and nothing in a VR session restores it, so the gallery and
    its tabs are the monitors' half of the mode and reachable only from a
    desktop session. A session hosting none is still a config naming none.
  - The main player verbs the VR main role does not implement: loop recording, version cycling,
    clip jumps, funscript jumps, length modes, compilations. They report unhandled,
    and the player logs each once rather than crashing. The list with a reason
    per verb is `fun_time_vr.roles.UNIMPLEMENTED_MAIN_PLAYER_VERBS`, and it is the only
    place a control may be left dead in the headset:
    `tests/test_vr_control_parity.py` walks every hotkey and every spoken phrase
    through the real dispatch and holds each verb that lands to the vocabulary of
    whatever will read it in VR, so a gap is a red test rather than a discovery
    in the log.
  - Every notice a command raises is a desktop overlay window the session does not
    launch, so a key that only flashes a confirmation on the desktop (`X` in a VR
    session, say) shows nothing in the headset. What the panel and the satellite
    HUDs draw is unaffected: those are in-scene surfaces, not windows.
  - With `vr.compositor_layers` on, the controllers, their laser, the handles and
    the spot the laser lands on draw in the projection layer, which the runtime
    composites beneath the satellites' quads; the pointer still works there,
    unseen. Off (the default, and the only mode the bundled runtime shows
    screens in) everything draws in one layer and the chrome sits on top.
  - For the same reason a squeeze cannot bring a screen forward across that
    split: with it on, the videos taken as quads stay in front of the console,
    the dashboard and the reference, and the satellites in front of the main
    player, whichever of them was taken hold of last.
- Notes:
  - Recorded here rather than in module docstrings (2026-08-30, audit item 25):
    the same deferral was once written out in five places, free to drift apart,
    and invisible to anyone grepping for a TODO marker before starting work.

## Genau Disable / Re-enable Reliability

- Status: Resolved (2026-09-15) by removing the switch
- Symptom: Toggling the takeover off and back on again behaved inconsistently.
- Resolution: the OSR2's own auto mode is always accepted and wins over
  everything else sending to the device, so there is no switch to toggle.

## OmniPause Does Not Fully Drop Fun Time Windows From Topmost

- Status: Resolved (2026-07-05)
- Symptom: Pressing `Esc` enters OmniPause, but Fun Time-managed windows can still remain effectively on top of other windows.
- Scope:
  - The primary display
  - The satellites
  - MFP
  - Fun Time overlay/dashboard
  - Genau
- Notes:
  - Several controller-side attempts were made to force topmost off during OmniPause.
  - Those attempts did not resolve the issue reliably enough to justify carrying more AHK-specific complexity while the Windows bridge is actively being reduced toward a thinner hotkey/window listener.
  - This should be revisited after more window-management responsibility has been extracted out of `windows_bridge.ahk`.
- Resolution: Window management now lives entirely in the Python bridge, whose OmniPause pass (`_remove_all_topmost`) drops every topmost-flagged window. The last window that stayed pinned was the main player: startup blanket-promoted every window to topmost, but OmniPause consulted a per-role policy where the main player is intentionally non-topmost (it rides under Genau's HUD), so the un-topmost pass skipped it and never released it. Both sides now read one shared `ROLE_TOPMOST` policy (`fun_time/window_roles.py`), and startup applies each window's own flag instead of forcing all-topmost — so startup and OmniPause can no longer disagree. (MFP no longer exists.)

## Python Dashboard Text Rendering Is Ugly

- Status: Deferred
- Symptom: After moving the Fun Time dashboard rendering from AHK to the Python/Tk dashboard app, the overlay appears in the correct place and is functionally usable, but the text rendering/layout looks noticeably worse than the old AHK version.
- Notes:
  - This is currently treated as visual polish debt, not a blocker for the extraction effort.
  - The immediate architectural goal is to keep moving dashboard and runtime responsibilities out of `windows_bridge.ahk`.
  - Future follow-up should improve typography/text wrapping/rendering in the Python dashboard without moving the UI back into AHK.
