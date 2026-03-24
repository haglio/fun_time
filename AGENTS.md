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

1. Add or update tests first when fixing behavior regressions.
2. Make the smallest targeted code change that gets the tests green.
3. Run the full suite again:
   `.\.venv\Scripts\python.exe -m pytest`
4. Keep commits small and single-purpose.
5. Update `docs/refactor-log.md` when the architectural shape or working norms change.

## Current Repo-Specific Gotchas

- Broker startup should flow through `launch_broker_tray.vbs`, not directly to `scripts/run_broker_service.ps1`, so the tray icon and service startup stay in sync.
- Chrome overlay tab opening is sensitive to window focus. Changes there should preserve explicit targeting of the Chrome window.
- The canonical test environment is the project `.venv`, not the system Python or Conda Python.
