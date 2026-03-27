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

### 4A. Audit and delete low-value mock-only tests ✅

Audited all 52 test files. Deleted 7 pure mock-only tests from priority modules:
- `test_command_dispatch.py`: 2 tests (robot_toggle/link_toggle delegation)
- `test_windows_bridge_dispatch_loop.py`: 2 tests (omnipause/file dialog routing)
- `test_windows_bridge_sequencer.py`: 3 tests (position_pid_window, rfb disabled)

Non-priority modules (orchestrator, audio_companion, startup) were audited and **kept** — their mock assertions test IO-bound code (subprocess, pygame, Win32) where mocks ARE the observable output. The 4A criteria applies to logic modules with real state, not IO wrappers.

### 4B. Replace mock-heavy tests with behavior tests ✅ (priority modules)

Replaced the 7 deleted mock-only tests with 6 behavior tests that assert real state:
- `test_command_dispatch.py`: 3 new tests verify state changes, file contents, and window ops
- `test_windows_bridge_dispatch_loop.py`: 2 new tests verify runner.state + shared state file
- `test_windows_bridge_sequencer.py`: 1 new test verifies no move_window calls when disabled

Core logic modules already at gold standard (no work needed):
- `test_lock.py`, `test_omnipause.py`, `test_modes.py` — pure state-driven, zero mocks
- `test_runtime_flow.py` — monkeypatch + captured calls, zero mock assertions
- `test_broker_com.py` — FakeSerialPort + real wiring (reference implementation)

### 4C. Add mutation testing (mutmut) — BLOCKED

mutmut does not support native Windows (requires WSL). mutatest also fails on Windows + Python 3.14. Deferred until WSL is available or a Windows-compatible mutation tester emerges.

Target modules when unblocked:
- `command_dispatch.py`
- `runtime_flow.py`
- `lock.py`
- `omnipause.py`
- `modes.py`
- `broker_protocol.py`

### 4D. Add property-based testing (hypothesis) ✅

Added 20 property-based tests in `test_property_based.py` using hypothesis:
- `broker_protocol.py`: `parse_auto_transition`, `RE_BPM`, `RE_STROKE` fuzz — never crash on arbitrary strings
- `vlc_actions.py`: `decode_file_uri` fuzz + XML parsing regexes — never crash, correct empty returns
- `media_actions.py`: `csv_escape` invariants (always quoted, internal quotes doubled), `to_file_uri` prefix, `make_web_url_from_path` site routing

All 20 tests pass with default hypothesis settings (100 examples per test).

---

## Phase 5: TEST SUITE OVERHAUL — Coverage ✅ (mostly)

### 5A. Fill critical coverage gaps ✅

All modules on the original list now have test files:
- `robot_hand/engine.py` — 6 tests covering phase advance, BPM smoothing, sync pulse, dt clamping
- `robot_hand/refresh_controller.py` — 6 tests
- `robot_hand/clip_loader.py` — 5 tests
- `robot_hand/clip_renderer.py` — 3 tests
- `robot_hand/clip_selection.py` — 5 tests
- `robot_hand/lifecycle.py` — 5 tests
- `robot_hand/notifier.py` — 4 tests
- `robot_hand/video.py` — 11 tests
- `dashboard_actions.py` — constants only, no logic to test

Remaining untested but no action needed:
- `orchestrator_broker.py` — dead code (functions duplicated in `orchestrator.py`; only constants/kwargs used)
- `manifest.py` — tested indirectly via `test_orchestrator.py::TestControllerManifest`
- `robot_hand/app.py` — tkinter entry point / wiring

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
