# Integration Runs

The record of this repo's integration suite — the one that launches real
players, a real dashboard and a real AHK bridge, and so can only run on the
user's Windows machine, off-screen, through:

    .venv/Scripts/python.exe -m tests.integration.hidden_desktop

There is no CI for it and there is not going to be one; the runner writes this
file itself, appending one row as it tears each run down.  So this is the whole
answer to "when did the integration suite last pass, and against what?" — and
the scope column is what keeps a green `-k nau` from reading as a green suite.

Oldest first, append-only, one row per run.  A SHA marked `-dirty` means
that checkout had uncommitted work when the run finished, so the commit
alone does not describe what ran.

| finished (UTC) | fun_time | player_core | result | counts | scope |
| --- | --- | --- | --- | --- | --- |
