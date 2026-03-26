## Handoff: Windows Bridge Minimization, Integration Coverage, Dashboard Pause

Date: 2026-03-25

### Why the work pivoted

The original task was to keep developing the Fun Time dashboard. That work exposed a larger architectural problem:

- the old `controller.ahk` had become an overloaded executable script
- it mixed hotkeys, launch/bootstrap, UI rendering, state management, media mutations, runtime orchestration, and external-app coordination
- dashboard iteration inside that surface was slow, brittle, and hard to validate

Because of that, the work intentionally pivoted from dashboard feature iteration to:

1. de-AHK-ifying the app toward a minimal Windows bridge
2. improving real launch/runtime verification so regressions would be caught by the agent instead of by the user first

### Dashboard status

The dashboard is no longer rendered by AHK.

Current state:

- live dashboard rendering now lives in Python (`fun_time.dashboard_app`)
- AHK/Windows bridge now provides only launch geometry and raw bridge-owned state
- Python derives dashboard-visible state, hydrates live VLC/media state, and owns the UI rendering

Known dashboard status:

- the Python dashboard appears and is usable enough to keep the architecture moving
- text rendering/layout quality is still poor and was explicitly deferred
- there is already a queued set of dashboard issues to resume once the Windows bridge minimization is complete

See also:

- `docs/known-issues.md`
- `docs/refactor-log.md`

### Windows bridge progress

The canonical AHK entrypoint is now:

- `windows_bridge.ahk`

`controller.ahk` is now only a backward-compatibility shim.

Large slices moved out of AHK into Python over the course of this refactor:

- dashboard rendering
- dashboard state derivation
- dashboard raw snapshot writing
- lock/discard planning and execution
- F-mode planning and execution
- Robot Hand planning and much of its non-window side effects
- OmniPause planning and much of its non-window side effects
- VLC write-side actions
- some VLC query helpers
- window layout planning
- startup coordination
- runtime-flow coordination
- Random Favs Browser planning
- broker heartbeat / dashboard status derivation

The remaining AHK surface is much smaller and much more honestly “Windows bridge” shaped than before, but the minimization is not fully complete yet.

The intent from here is:

- keep only what AHK is actually comparatively good at:
  - global hotkeys
  - direct Windows window/input glue
  - small amounts of topmost/activate/move/show/hide behavior
- continue moving the remaining runtime/application behavior to Python

### Integration-testing progress

The repo now has a real opt-in Fun Time integration layer instead of relying only on unit/contract tests.

Key improvements made:

- added a real launch smoke path
- added a real opt-in integration harness
- moved from one broad end-to-end scenario to focused scenarios
- then reduced startup overhead by reusing a longer-lived shared session for non-destructive tests

Current focused integration coverage includes:

- startup/runtime smoke
- portrait lock/unlock
- omnipause toggle
- F-mode toggle
- Robot Hand enable/disable
- Robot Hand mode-file sync
- portrait discard/weird flow

Important testing philosophy changes:

- the user should not be the first detector of “did it launch at all?”
- launch/component regressions should be reproduced locally first
- local logs/state/runtime evidence should be inspected by the agent proactively

Recent important integration change:

- integration startup now bypasses the broker tray VBS/PowerShell script-host chain
- when `FUN_TIME_RUN_INTEGRATION=1`, broker startup is direct Python startup instead
- this was done specifically because live integration runs surfaced permission/script popups

### Integration harness status and caveats

The normal suite is in good shape and currently green:

- `743 passed, 7 skipped`

The integration harness is significantly better than before, but not “finished forever.”

Notable current improvements:

- integration actions are driven by command/state seams, not real global hotkeys
- the unused AHK hotkey injection helper was removed
- live integration temp roots were moved away from hard dependency on pytest’s default workspace-local base temp
- `tests/conftest.py` now allows overriding the unit-test temp root with `FUN_TIME_PYTEST_TMP_ROOT`

Still deferred for later:

- temporary mute during integration
- broker COM-level mocking
- any additional “quiet mode” or lower-disruption integration refinements

### Recommended next step

Resume the Windows bridge minimization before returning to dashboard feature work.

The right next move is:

1. inspect the remaining AHK files/includes
2. identify what still counts as application/runtime behavior rather than genuine Windows glue
3. extract more of that to Python
4. stop when the remaining AHK surface is defensibly just a thin Windows bridge

Only then resume the deferred dashboard issue queue from the new architectural baseline.

### Practical note for the next agent

If a future change affects launch/runtime behavior:

- use the real integration/smoke tooling first
- inspect logs locally
- do not bounce “did it appear?” checks to the user first

If a future change expands the responsibility of the AHK bridge:

- pause and reassess
- prefer Python extraction over growing the bridge
