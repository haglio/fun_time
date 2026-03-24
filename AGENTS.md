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
   Independent fixes or refactors should land as separate commits so they are easy to review, reason about, and revert.
6. Document for future agentic work.
   Update `docs/refactor-log.md` when the architectural shape or working norms change, and update README/other docs when runtime contracts, workflows, or operator expectations change.
7. Leave a clean handoff.
   Before finishing, make sure the worktree is clean, the tests are green, temporary exploration artifacts are removed, and the repo is in a good state for the next feature or fix.
8. Treat executable script syntax as a separate validation concern.
   When changing `controller.ahk`, `.ps1`, `.vbs`, shell wrappers, or other directly executed script files, do not rely only on text-based Python tests. Reuse an existing in-repo quoting/style pattern when possible, add a regression test for the intended contract, and run at least one syntax/startup-focused validation step before handoff so the script still opens cleanly.
9. Escalate when executable-script scope starts expanding into product/UI work.
   If a change would substantially increase the size or responsibility of `controller.ahk` or another executable script, pause and document the tradeoff before continuing. Prefer extracting logic into Python/modules or a dedicated UI surface when the work is no longer "glue code", especially for stateful dashboards, layout-heavy UI, or multi-step interaction flows.

## Current Repo-Specific Gotchas

- Broker startup should flow through `launch_broker_tray.vbs`, not directly to `scripts/run_broker_service.ps1`, so the tray icon and service startup stay in sync.
- Chrome overlay tab opening is sensitive to window focus. Changes there should preserve explicit targeting of the Chrome window.
- The canonical test environment is the project `.venv`, not the system Python or Conda Python.
