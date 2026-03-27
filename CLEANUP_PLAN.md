# Fun Time Cleanup Plan

Generated 2026-03-26 by deep codebase review. Clipper has been extracted to a separate project.

## Current State

- **Source**: 67 modules, ~8,800 lines in `fun_time/`
- **Tests**: 68 files, ~580 tests, ~9,900 lines
- **AHK**: 129 lines (minimal hotkey shell — done)
- **Archive**: 12 dead Python files + media/build artifacts still present
- **Test:source ratio**: ~1:1 by line count, but test quality is poor (97.7% mock density, many tests assert mock.called rather than behavior)

---

## Phase 1: PRUNE DEAD CODE

Low risk, immediate payoff. Delete code that is never reached.

### 1A. Delete `archive/` directory

12 Python files + media + build artifacts (listen_only.py, tiny_logger.py, make_frame_variants.py, osr2_axis_reader.py, osr2_broker.py, osr2_image_visualizer.py, osr2_image_visualizer_1.py, osr2_image_visualizer_broken.py, osr2_passthrough_test.py, osr2_visualizer_app.py, robot_hand_audio_companion.py, robot_hand_listener.py, plus .mp4 files, build/, dist/, frames_*/, etc.). Zero references from active codebase.

### 1B. Delete dead `_app.py` CLI wrappers

The AHK→subprocess→plan-file dispatch pattern was replaced by the Python dispatch loop calling logic directly via `bridge_command_dispatch.py`. These 10 `_app.py` files are no longer invoked as subprocesses:

| File | Lines | Why dead |
|------|-------|----------|
| `windows_bridge_modes_app.py` | 67 | `bridge_command_dispatch` imports `windows_bridge_modes` directly |
| `windows_bridge_lock_app.py` | 79 | dispatched in-process |
| `windows_bridge_omnipause_app.py` | 86 | dispatched in-process |
| `windows_bridge_robot_hand_app.py` | 79 | dispatched in-process |
| `windows_bridge_dashboard_bridge_app.py` | 41 | dispatched in-process |
| `windows_bridge_random_favs_browser_app.py` | 66 | dispatched in-process |
| `windows_bridge_vlc_actions_app.py` | ~60 | dispatched in-process |
| `windows_bridge_window_layout_app.py` | 67 | dispatched in-process |
| `windows_bridge_startup_app.py` | ~60 | dispatched in-process |
| `windows_bridge_runtime_flow_app.py` | 207 | dispatched in-process |

**Verification step**: Before deleting each, grep for subprocess invocations that reference its module name. Confirm zero hits.

**~810 lines deleted.**

### 1C. Delete dead `_app` test files

After removing the wrappers, delete their test files. For each, check if the test exercises logic worth keeping — if so, migrate the test to target the non-`_app` module, then delete the wrapper test.

Files to audit:
- `test_windows_bridge_lock_app.py`
- `test_windows_bridge_modes_app.py`
- `test_windows_bridge_omnipause_app.py`
- `test_windows_bridge_robot_hand_app.py`
- `test_windows_bridge_dashboard_bridge_app.py`
- `test_windows_bridge_random_favs_browser_app.py`
- `test_windows_bridge_vlc_actions_app.py`
- `test_windows_bridge_window_layout_app.py`
- `test_windows_bridge_startup_app.py`

### 1D. Check `media_actions_app.py`

This is a CLI wrapper around `media_actions.py`. Verify whether it's still invoked as a subprocess anywhere. If not, delete it and its test.

---

## Phase 2: RENAME MODULES

Now that Python is the orchestrator (not AHK), the `windows_bridge_` prefix on pure-Python logic modules is misleading.

### 2A. Drop `windows_bridge_` from pure-Python modules

| Current name | Proposed name | Reason |
|-------------|---------------|--------|
| `windows_bridge_monitors.py` | `monitors.py` | Pure ctypes monitor enumeration |
| `windows_bridge_win32.py` | `win32.py` | Pure ctypes Win32 wrappers |
| `windows_bridge_window_layout.py` | `window_layout.py` | Pure layout computation |
| `windows_bridge_runtime_flow.py` | `runtime_flow.py` | Pure state-transition logic |
| `windows_bridge_dashboard_bridge.py` | `dashboard_bridge.py` | Pure snapshot writer |
| `windows_bridge_omnipause.py` | `omnipause.py` | Pure plan builder |
| `windows_bridge_lock.py` | `lock.py` | Pure plan builder |
| `windows_bridge_modes.py` | `modes.py` | Pure playlist builder |
| `windows_bridge_robot_hand.py` | `robot_hand_plan.py` | Pure plan builder (avoid collision with subpackage) |
| `windows_bridge_vlc_actions.py` | `vlc_actions.py` | Pure HTTP API wrapper |
| `windows_bridge_manifest.py` | `manifest.py` | Pure config generation |

**Keep the prefix** only for modules that genuinely orchestrate the AHK/Python bridge:
- `windows_bridge_orchestrator.py` — launches AHK
- `windows_bridge_sequencer.py` — startup sequence
- `windows_bridge_dispatch_loop.py` — polls command files

### 2B. Rename `bridge_command_dispatch.py` → `command_dispatch.py`

The "bridge" in the name is a holdover from when commands came from AHK.

### 2C. Update all imports, test files, and AGENTS.md/CLAUDE.md references

Systematic find-and-replace across the codebase for each rename. Update test file names to match (e.g., `test_windows_bridge_lock.py` → `test_lock.py`).

---

## Phase 3: DRY FIXES

### 3A. Extract shared `_to_bool()` / bool-parsing utility

Identical `_to_bool()` implementations exist in multiple `_app.py` files (which may be deleted in Phase 1). Check if the dispatch loop or other surviving code also has inline bool parsing. If so, consolidate into `runtime_support.py`.

### 3B. Extract shared INI-writing utility

Multiple modules write configparser INI files with identical boilerplate (create parent dir, write sections, atomic replace). Extract a `write_ini_file(path, sections_dict)` helper to `runtime_support.py`.

---

## Phase 4: TEST SUITE OVERHAUL — Quality

This is the highest-value long-term investment.

### 4A. Audit and delete low-value mock-only tests

Tests that only assert `mock.called` or `mock.assert_called_once_with(exact_args)` without verifying any state change or observable output are net-negative. They lock in implementation details and provide false confidence. Identify and delete these.

**Criteria for deletion**: If removing the test and introducing a bug in the production code would NOT cause any test to fail, the test is low-value.

### 4B. Replace mock-heavy tests with behavior tests

For core logic modules, write integration-style tests that wire real objects together. The `test_broker_com.py` pattern (FakeSerialPort wiring real protocol + session) is the gold standard in this codebase. Priority modules:

1. `bridge_command_dispatch.py` — command dispatch state machine
2. `windows_bridge_runtime_flow.py` — omnipause/fmode/robot transitions
3. `windows_bridge_dispatch_loop.py` — dispatch loop coordination
4. `windows_bridge_sequencer.py` — startup sequence

### 4C. Add mutation testing (mutmut)

Install mutmut and run against core logic modules to verify tests catch real bugs:
- `bridge_command_dispatch.py`
- `windows_bridge_runtime_flow.py`
- `windows_bridge_lock.py`
- `windows_bridge_omnipause.py`
- `windows_bridge_modes.py`
- `broker_protocol.py`

Use mutation survival rate to identify which tests need strengthening.

### 4D. Add property-based testing (hypothesis)

Prime candidates for generative/fuzz testing:
- `config.py` — fuzz JSON config loading with arbitrary structures
- `broker_protocol.py` — fuzz serial message parsing
- `windows_bridge_vlc_actions.py` — fuzz HTTP response parsing
- `media_actions.py` — fuzz CSV manipulation

---

## Phase 5: TEST SUITE OVERHAUL — Coverage

### 5A. Fill critical coverage gaps

Source modules with zero test coverage, in priority order:

1. `orchestrator_broker.py` — broker lifecycle (critical path)
2. `robot_hand/engine.py` — BPM/phase math (pure functions, easy to test)
3. `robot_hand/refresh_controller.py` — main refresh loop
4. `robot_hand/clip_loader.py` — background clip loading
5. `robot_hand/clip_renderer.py` — frame rendering
6. `robot_hand/clip_selection.py` — clip switching logic
7. `robot_hand/lifecycle.py` — event binding and shutdown
8. `robot_hand/notifier.py` — UDP notification
9. `robot_hand/video.py` — video file I/O
10. `dashboard_actions.py` — trivial constants, low priority

### 5B. Expand integration test suite

Integration tests are now always-on for Windows (good). Consider adding:
- Integration tests for the dispatch loop (wire real dispatch + real state files)
- Integration tests for the robot hand UDP listener (real sockets, small test clips)

---

## Phase 6: ARCHITECTURE IMPROVEMENTS

### 6A. `dashboard_app.py` (508 lines) — extract widget logic

Largest source file. Split into:
- `dashboard_widgets.py` — custom widget creation and rendering
- `dashboard_events.py` — button click handlers, polling logic
- Keep `dashboard_app.py` as the thin entry point

### 6B. `robot_hand/app.py` (214 lines) — extract initialization

Heavy initialization logic creates 10+ controller objects. Extract a `robot_hand/factory.py` or `robot_hand/wiring.py` that builds the controller graph, keeping `app.py` as just the entry point.

### 6C. `robot_hand/runtime_commands.py` — clean up polymorphic accessors

`get_engine_phase()` / `set_engine_phase()` handle both dataclass and dict, suggesting backward-compat code. Verify if the dict path is still used. If not, simplify to dataclass-only.

---

## Phase 7: POLISH (opportunistic)

### 7A. Consistent error handling

One bare `except: pass` in `windows_bridge_dispatch_loop.py` `_update_dashboard()`. Consider at minimum logging the exception.

### 7B. `robot_hand/clip_loader.py` — deduplicate loader/prefetch threads

`_loader_thread_fn()` and `_prefetch_thread_fn()` are nearly identical. Extract shared logic.

### 7C. Consistent `__all__` exports

Some modules define `__all__`, most don't. Either adopt it everywhere or nowhere.

---

## Execution Notes

- **TDD discipline**: For every deletion or refactor, run the full test suite before and after. For new tests, red-green-refactor.
- **Commit granularity**: One commit per logical step (e.g., "delete archive/", "delete windows_bridge_modes_app.py and test", "rename windows_bridge_monitors → monitors").
- **Integration tests**: Run integration tests after any runtime behavior change, not just unit tests.
- **Dead code cleanup**: After each deletion, grep for newly-orphaned references (imports, constants, etc.) and remove them in the same pass.
