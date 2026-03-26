# Agent Working Notes

Read this file before making changes in this repo.

## Mandatory Preflight

1. Confirm the repo is clean enough to work in:
   `git status --short`
2. Run the full test suite before and after substantive changes.

Preferred command in this Windows repo:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Convenience wrapper:

```powershell
bash test.sh
```

If `bash test.sh` fails in PowerShell because Git Bash cannot create its signal pipe, use the direct `.venv` command above. `test.sh` is only a thin wrapper around that same command.

## Change Workflow

1. Start with tests.
   When fixing regressions or changing behavior, add or update characterization coverage first so the failure is visible in the suite before changing production code.
2. Use a red-green-refactor loop.
   Make the test fail for the right reason, implement the smallest targeted fix to get green, then refactor against the green suite to keep the codebase cleaner than you found it.
3. Aim for solid regression coverage, not minimal box-checking.
   Prefer tests that lock down the user-visible contract or module seam that actually broke, especially around launch paths, controller flows, and automation-sensitive behavior.
4. Run the full suite after substantive changes:
   `.\.venv\Scripts\python.exe -m pytest`
5. Keep commits small and single-purpose.
   Independent fixes or refactors should land as separate commits so they are easy to review, reason about, and revert. Commit after each logical step — do not accumulate an entire session of changes into one giant commit.
6. Clean dead code as you go.
   When a refactor makes code unreachable — unused globals, orphaned functions, stale constants, obsolete imports — delete it in the same pass. Do not leave dead code behind for a future cleanup. After each extraction or simplification, actively search for newly-dead references (grep for the thing you just stopped calling) and remove them. This applies to tests too: update or delete contract tests whose assertions describe the old architecture.
7. Document for future agentic work.
   Update `docs/refactor-log.md` when the architectural shape or working norms change, and update README/other docs when runtime contracts, workflows, or operator expectations change.
8. Rename alongside refactors.
   When a refactor changes a module or entrypoint's real responsibility, update the names in the same pass. Do not leave stale names behind to be "cleaned up later" if they now misdescribe the architecture.
9. Simplify designs as you refactor.
   Each extraction pass is an opportunity to simplify the interface between layers. If Python now reads a manifest directly, remove the CLI args that used to pass the same values through AHK. If a state variable is already tracked by a sync loop, delete the function that reads it from disk. Prefer eliminating indirection over preserving backward compatibility within the same codebase.
10. Leave a clean handoff.
   Before finishing, make sure the worktree is clean, the tests are green, temporary exploration artifacts are removed, and the repo is in a good state for the next feature or fix. Explain what you think the best next step is — what's now unblocked, what's the highest-leverage remaining work, and whether the project is ready to return to feature work or still needs structural cleanup.
11. Inspect local runtime evidence proactively.
    When debugging controller/runtime behavior, check the relevant local logs, state files, and other runtime artifacts yourself before asking the user to gather or relay them. Ask the user to reproduce or confirm behavior when needed, but do not offload basic local log inspection to them.
12. Reproduce launch/component regressions locally before asking for manual verification.
    If a change affects whether Fun Time or one of its startup components launches at all, do not use the user as the first detector. Reproduce the launch locally yourself first, using the repo's runtime artifacts and, when needed, an actual app launch/smoke run. Manual user checks are appropriate for subjective UI review or hard-to-automate operator workflows, not for basic questions like "did the component appear?"
13. Prefer reusable launch diagnostics over one-off guesses.
    When a launch/startup issue is hard to see from tests alone, improve the repo's ability to inspect it: add or update targeted logging, state markers, or a local smoke/inspection script so future agents can verify the same class of problem with less guesswork.
14. Do not parallelize live Windows bridge launches.
    `windows_bridge.ahk` still runs under `#SingleInstance Force`, so real startup checks, integration runs, smoke runs, and direct AHK launch validations must be executed sequentially. Parallel live launches can evict each other and create false-negative runtime signals.

## Architecture Escalation

When preparing a refactor plan or implementing substantial feature work, explicitly redacted whether any executable script or entrypoint has accumulated multiple responsibilities.

Examples of concentration risk:
- hotkeys plus business logic
- UI rendering plus external app orchestration
- file mutation plus state management
- launch/bootstrap code plus product behavior

If an executable script has become a concentration point:
1. Call it out explicitly as an architectural risk.
2. Prefer extracting business logic and UI/state logic into testable Python modules before adding substantial new features.
3. Do not treat the existing entrypoint as the default home for new product logic just because it already launches the workflow.
4. If the file name no longer matches its real responsibility, note the naming mismatch and propose a clearer post-extraction name.
5. In refactor plans, include at least one step that reduces the executable script's responsibility instead of only reorganizing code around it.
6. When an extraction changes the truthful boundary, rename the surviving bridge/module in the same pass so the codebase vocabulary keeps up with the architecture.
7. Treat executable script syntax as a separate validation concern.
   When changing `windows_bridge.ahk`, `controller.ahk`, `.ps1`, `.vbs`, shell wrappers, or other directly executed script files, do not rely only on text-based Python tests. Reuse an existing in-repo quoting/style pattern when possible, add a regression test for the intended contract, and run at least one syntax/startup-focused validation step before handoff so the script still opens cleanly.
8. Escalate when executable-script scope starts expanding into product/UI work.
   If a change would substantially increase the size or responsibility of `windows_bridge.ahk`, `controller.ahk`, or another executable script, pause and document the tradeoff before continuing. Prefer extracting logic into Python/modules or a dedicated UI surface when the work is no longer "glue code", especially for stateful dashboards, layout-heavy UI, or multi-step interaction flows.

## Current Repo-Specific Gotchas

- Broker startup should flow through `launch_broker_tray.vbs`, not directly to `scripts/run_broker_service.ps1`, so the tray icon and service startup stay in sync.
- Random Favs Browser tab opening is sensitive to window focus. Changes there should preserve explicit targeting of the Chrome window.
- The canonical test environment is the project `.venv`, not the system Python or Conda Python.
