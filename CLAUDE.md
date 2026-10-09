# fun_time — Project-Specific Instructions

Shared rules are in the global `~/.claude/CLAUDE.md`. This file contains only fun_time-specific overrides.

**Keep this file short.** No redundancy with the global CLAUDE.md. One bullet per rule. If editing this file, remove or consolidate — never just append.

## Test commands

Unit tests (run freely, no permission needed):
```powershell
.\.venv\Scripts\python.exe -m pytest
```

The default `pytest` invocation only runs unit tests. Integration tests live in `tests/integration/` and are excluded from the default collection.

**Never pass `-c` to pytest.** The config lives in `pyproject.toml`; naming any other file gives pytest a config with no `addopts` and no `norecursedirs` — that switches off the sanitize guard and sweeps `tests/integration/` into the run, putting real players, a real main player and a real AHK bridge on the user's monitors, over his work, while he cancels them and you read the wreckage as your change failing. `tests/test_pytest_config.py` keeps the settings in one file; `tests/integration/conftest.py` now refuses any run that reaches an integration test off the hidden desktop.

Integration tests — run on a hidden Win32 desktop so the real windows never touch your screen; safe to run unattended, like unit tests, **including while Fun Time is open**:
```powershell
.\.venv\Scripts\python.exe -m tests.integration.hidden_desktop
```

The runner creates the hidden desktop, sets `FUN_TIME_RUN_INTEGRATION=1`, and runs the whole suite invisibly (real HWNDs, off-screen, never foreground) and below normal priority, every process of it, so a run gives way to his live session instead of making the OSR2 falter; expect it to run slower while he is using the machine. The runner's machine-wide lock serializes concurrent agent runs — a second run queues instead of clobbering, so you don't hunt for a quiet window. Extra pytest args pass through. **Try a new check first with its file alone** (`... hidden_desktop -k <file stem>`, or the file's path): a run narrowed that way, or by `--repeat-changed`, goes ahead of full runs in the line until short runs have held it for 20 minutes while a full run waited.

A run other sessions are waiting for is a formality, never the place a check is first tried: a failure there costs a whole run plus the wait in line, again for every retry, and holds up every session queued after it. So before a branch goes into the machine-wide run, every check it adds or changes has already passed in the quickest place that can run it — the unit suite for anything a fake can stand in for, and otherwise the whole file the check lives in (`... hidden_desktop -k <file stem>`), never the check by itself, because the tests beside it are what a full run puts in front of it, and a check that passes alone can fail once they have run first. Running the file only after a full run has failed spends the shared run on what one run of the file would have shown.

**Before landing a branch that adds or changes an integration test, run `... hidden_desktop --repeat-changed`** (or `--repeat-changed <base>`). It runs each file holding an integration test the branch added or changed whole, once, then each such test ten times on a busy machine, and fails if one run fails, so a flaky test is fixed by the session that wrote it instead of costing another session one. The merge gate does the same for unit tests on every pull request, but no runner can host these, so this run is the only one they get. The gate runs from an install of its own under `state/flake_gate_venv`, made on first use, while the repeated tests run in this venv. It spends at most 45 minutes on runs and names any test it could not reach, so a branch that changes a whole file still comes back with an answer. Run no other pytest in that worktree while it goes: each writes `.coverage` where it stands, and the gate's next run then dies at startup on the other's open file, which it reports as your test failing.

A run reaches nothing of the user's, so it never has to wait for one. Two mechanisms, and every shared resource belongs to one of them: the hidden desktop covers everything with a per-desktop version (windows, focus, input hooks, AHK's single-instance search, the leftover-process reap), and `integration_support.isolate_shared_resources` strips out everything without one (the three UDP ports, the loopback port, the broker's tray launcher, the microphone) — see its docstring for what each collision was. The broker, voice and the Random Favs Browser (which a run also switches off) therefore never run in the suite, so a change that reaches one needs a unit test that opens a session with it on: a stale caller on the browser's startup path passed both suites on 2026-09-12 and crashed his first Verify run of that branch. Adding a new machine-global resource means adding it there; `test_integration_support.py` sweeps a run's config for any surviving mention of the machine's endpoints. What is still shared is the GPU, so a run and a live session compete for decode.

Run the suite only through `hidden_desktop`: `pytest tests/integration/` refuses at session start (exit `4`), because that form puts real windows, a real AHK bridge and real players on your own desktop.

**Green means every collected test passes — zero failures, skips, or deselects.**

`tests/test_dead_code.py::test_prose_does_not_outgrow_the_code_it_explains` is this repo's ceiling on comment and docstring lines across the packages and `tools/`, and it only ever ratchets down: a change that removes prose lowers `MAX_PROSE_LINES` with it, so what it freed cannot be spent by the next change.

Convenience wrapper (unit tests only): `bash test.sh`
If `bash test.sh` fails because Git Bash cannot create its signal pipe, use the direct `.venv` command above.

## Win32 API changes: mandatory pre-flight

Before modifying any Win32 API call (ctypes, keyboard/mouse input, window management, thread input):

1. **State the mechanism.** Explain WHY the approach works, citing the specific Win32 behavior it depends on.
2. **Verify the claim.** If not certain, say so explicitly rather than guessing.
3. **Check interactions.** Identify what other components touch the same subsystem (AHK hooks, the Qt event loop under the dashboard and overlays, the players' own windows, thread input queues) and explain why the change won't break them.
4. **Map from symptoms.** Trace the execution path that produces the bug and confirm the fix addresses that specific path.

If you cannot complete these steps, stop and say so. Do not submit a speculative fix.

## Standing rules a comment cannot enforce

- **A player in his words is the room's slot, never the app in it.** The Main, Portrait and Landscape players are the players; Kino, Genau and Origenerator are what plays in them. Kino is the mode that used to be called Video, renamed so that "video" can only ever mean a video file; older notes and memories still say "video mode", and none of it reaches him. A feature he gives "the Main player" holds in Genau mode too, although `main_player/` is only Kino's: the crown shipped Kino-only and he answered that Genau was the main reason for it (2026-09-26). Each of those players IS a Funestra, player_core's window that plays what it is handed and draws its own HUD: a player never "runs on" or "has" one, and Kino, Genau and an Origenerator Slideshow are what runs on it. The near miss that still counts: "the satellites run on a Funestra" -- the satellite program is the window, so each satellite is one.

- **The two controllers are interchangeable: nothing in the headset gives the left and right hand different jobs, and a control the sticks and buttons cannot spare goes on the keyboard, on a key found by feel.** He uses one controller at a time; a wrapped picture's angle sat on the left stick and its height on the right, and he answered "I can't use both controllers at once. I treat the controllers in Fun Time as interchangeable, and the code should too" (2026-09-27). The angle went to Alt with Left and Right, in the arrow cluster.

- **The desktop and the headset are one surface: every change to the desktop reaches the headset in the same piece of work, and the headset runs the desktop's own code wherever it can rather than drawing a copy of it.** A copy is a second thing to keep in step, and it is out of date the moment the desktop moves on, so the headset ends up showing a control the desktop no longer has or missing one it gained. Where a desktop change cannot apply in the headset, say why in the same piece of work rather than leaving the gap to be found by hand.

- **Every running copy of this app is a surface, a preview included, and a handshake with Origenerator has to work whichever copy of each app is running.** A preview runs its worktree's code with a `state/` of its own, so a file one app writes into one checkout's `state/` for the other to read reaches only the copy that runs from there, and every other copy of the reader goes on as if the handshake never happened. So anything one app tells the other about the one device, the one library or the one machine is written where every copy of the reader looks — the `state/` of the reader's primary checkout — and read from there by every copy. A change to such a handshake is tried with a preview on each side before it is called done.

- **OmniPause's limits are settled on all three input paths: never widen one, never narrow one, and never ask him about any of them.** Spoken, a paused room answers exactly three things — resume, quit, retract the OSR2 (`SUSPEND_EXEMPT_COMMANDS`, pinned whole by `tests/test_command_reference.py`). The keyboard is the same three and nothing else: entering the pause suspends the AutoHotkey script wholesale, and `#SuspendExempt` keeps Esc, Ctrl+Alt+Q and Shift+Esc live through it — which is also what hands his keyboard back to everything else on screen, and is how he types into a hosted Origenerator window inside a session. Every button goes on working through a pause and must: the dashboard's, each player's, the headset controller's. A press carries no `spoken_at`, which is the whole of what `_frozen` keys off, and `test_keeps_the_mouse_live_under_omnipause` pins it. His words on all three, 2026-09-28: "keys shouldn't work during a pause either! Christ no. So not hotkeys or voice commands, other than the obvious exceptions we already have. But anything else, like buttons, should still work fine." The reference popup was exempt from the spoken freeze once and was walked back, so when a spoken command seems to need the pause lifted, the fix is to say on screen why it was ignored (2026-09-13). **The trap that has already cost him a round trip:** the three-command set is a VOICE set, so it is never why a button or a headset controller click did nothing. An agent told him it was; he ordered the limit lifted off everything but voice, and the work that followed turned out to be no work at all, the app having always behaved as he wanted.
- **Nothing is reported on the loading screen after the line that fills the bar.** A wait added at the end -- the hosted app's boot, say -- shows as a step past "Finalizing..." with the bar already full, which reads as a room that is finished and still working. So a wait under the cover gets its own phase before the final weightless one, carrying weight, and that weightless line stays last: the dashboard shows itself when the bar fills and the post-loading window pass waits for that window, so the line has to come before the pass.
- **Every launch entry point calls `apply_genau_dirs_to_sys_path()` before importing anything that reaches the dispatch loop.** `tests/test_launch_smoke.py` imports each the way its `.vbs` runs it and goes red for whichever one is missing it.
- **No key inside a satellite or a VR player ends that player.** A satellite is one of a set the sequencer placed, and killing one leaves the session running around a gap; the session ends as a whole, through Ctrl+Alt+Q. Do not add a per-window quit handler back.
- **The shells are not unit-tested, on purpose:** `satellite/app.py`, `fun_time_vr/player.py`, `fun_time_vr/render.py`, `fun_time_vr/vr_session.py`, `fun_time_vr/video_thread.py`, `fun_time_vr/gl_contexts.py`, `fun_time/chrome_accessibility.py` need the libmpv DLL, a GL context, a headset or a real Chrome. Logic that can be tested lives outside them — `fun_time.chrome_tabs`, `satellite.session`, `satellite.runtime`, `fun_time_vr.roles`, `fun_time_vr.genau_role`, `fun_time_vr.console_panel`, `fun_time_vr.pointer`, `fun_time_vr.layout`, `fun_time_vr.frame_relay`, `player_core.satellite_hud*` — and is tested against fakes. Put new logic there, not in a shell.

## AHK bridge constraints

`windows_bridge_hotkeys.ahk` runs under `#SingleInstance Force`. Startup checks, integration runs, and AHK launch validations must be executed sequentially — parallel launches can evict each other.

## Test fidelity

- **Test configuration must derive from production code — in every test that stands in for a session, unit tests included.** A test's launch commands, flags, init sequences and session configs come from the same production functions real sessions use, never hand-crafted: if the test builds its own satellite command line instead of calling `_build_satellite_launch_command`, it can pass while production is broken. A hand-built copy is also where a production decision changes without any test noticing: `tests/test_vr_control_parity.py` built its headset config by hand with Origenerator off, and went on passing for five days after `build_bridge_config_from_manifest` started hosting Origenerator in the headset.
- **Integration tests must randomize video selection.** Use `random.sample()` or `random.choice()` — never `sorted()[:n]` or other deterministic selection. The same videos playing every run masks bugs that only surface with different media files.
- **Look at the session's windows from the test's own thread, and find them by the session's own processes.** Reading the caption of a window the test process owns waits on the thread that made it, and the dashboard tests leave Qt windows in the process, so `iter_zorder` from a second thread stalls there for good; and a caption alone can land on a window an earlier session in the same run is still closing. A watcher thread that did both passed every run of its own file and timed out in every full run (2026-09-26).
- **A session built from the real config plays his real Genau clips folder.** The verbs that move a clip out of it or record its flip act on his own files, so a test that sends `genau_weird_clip` or `genau_flip_ends` gives the session a clips folder of its own, as `tests/integration/test_genau_weird_integration.py` does.

## The shared repos

- No app may reach into another's repo. What we share lives in siblings installed editable into this venv, and a change to any of it belongs there: `../player_core` (the players' engine, and the player contract: the playlist, the verbs, the paused flag, the status a player publishes and the HUD it is handed — each written and read there), `../app_support` (logging setup and exception hooks, `start_daemon_thread`, `preparse_config_path`, `hidden_subprocess_kwargs`), `../shared_ui` (Qt widgets).
- Install each with `--config-settings editable_mode=compat`; their READMEs say why, and each carries a `tests/test_install.py` that goes red without it.
- `satellite/` is a second top-level package in this repo, launched as `python -m satellite` with **our** python (`paths.python_exe`), not genau's. It resolves through the working directory `launch.vbs` sets, the same way `-m fun_time.dashboard_app` does.

## Repo-specific gotchas

- Broker startup flows through the broker checkout's `launch_broker_tray.vbs`,
  and `broker_contract.json` beside it is where that repo says what its
  processes are called and which directory its sources are in
  (`fun_time/broker_contract.py`). Nothing here spells any of it.
- Random Favs Browser tab opening is sensitive to window focus — preserve explicit Chrome window targeting.
- The test environment is the project `.venv`, not system Python or Conda.

## Test fixtures must be fabricated, never copied from the real library

Every fixture value that stands in for library data — a video title, a filename,
a performer or studio name, prompt text — must be **invented**. Never paste a
real one out of the media library to make a test feel realistic.

This is not a style note. It is the single thing that has actually leaked private
data into these repos: an agent writing a test reached for a real filename or
performer name because it was handy, and it rode into a public commit. Nothing in
the app's *design* pulls library text into source — the library lives outside
every repo, read at runtime through the git-ignored overlays — so this habit is
the only remaining path for a real name to get committed, and the only thing
stopping it is you following this rule.

Do not lean on the sanitize guard to catch it. `app_support.sanitize` fails
the suite when a **known** blocked term appears in the tracked tree, but a brand-
new performer name it has never seen passes every check and lands. The guard is a
backstop for names already known; it cannot see the next one.

So fabricate fully. Use `Jane Doe`, `Example Studio`, `scene one`, the
`alpha`/`beta`/`gamma` act placeholders the committed `content.example.json`
already uses. The near miss that still counts: taking a real filename and
changing a character or two — it is still that clip, still that performer. Make
it up from scratch, don't lightly edit a real one.

## Landing — GitHub merge queue, not local ff-merge

This repo is public at `github.com/haglio/fun_time` with a merge-queue ruleset on
`main`, so the global "ff-merge into the primary checkout under
`.git/agent-merge.lock`" flow does NOT apply here:

- **Land through a pull request.** From your worktree: commit, `git fetch origin
  && git rebase origin/main`, `git push -u origin <branch>`, then
  `gh pr create --fill`. Auto-merge arms itself; the queue rebases your PR onto
  `main`, runs the required check, and merges it when green. Don't ff-merge into
  the primary checkout, don't push `main` directly, and never force-push `main`.
- **Delete the branch the moment it merges** — `git push origin --delete
  <branch>`, before you tell him the work is done. Neither the queue nor the
  ruleset prunes it, so every landed PR otherwise leaves a branch on origin for
  good, and the next agent reading `git branch -r` for what is in flight has to
  sift the dead from the live.
- **The `.git/agent-merge.lock` is retired here** — the GitHub queue serializes.
- **Sync local checkouts by pulling.** `main` advances only on origin (via the
  queue), so the primary checkout and worktrees update with
  `git pull --ff-only origin main`; the running app self-updates the same way.
  The primary is only ever fast-forwarded — never reset or merged-into.
- **A red required check** (`.github/workflows/merge-gate.yml`) can't land.
- **A PR sitting with green checks has stalled in one of two ways, and only
  `isInMergeQueue` tells them apart.** `mergeStateStatus` reads `CLEAN` and
  `autoMergeRequest` reads null in BOTH, which is what invites a wrong
  diagnosis and a wrong report to him — ask GitHub directly instead: `gh api
  graphql -f query='{repository(owner:"haglio",name:"fun_time"){pullRequest(
  number:N){isInMergeQueue mergeStateStatus}}}'`.
  **In the queue, turning `DIRTY`:** another agent's PR merged first and yours
  no longer merges cleanly, so the queue waits, armed, forever. Rebase the
  moment it turns, and do not re-arm — re-arming does nothing, and on
  2026-09-07 bought twenty minutes of silence. The repo lands several agents'
  work an hour, so losing that race is ordinary and only failing to notice it
  is not.
  **Not in the queue, still `CLEAN`:** you force-pushed after the PR opened.
  GitHub drops auto-merge on a force-push, and `.github/workflows/auto-merge.yml`
  fires only on `opened`/`ready_for_review`/`reopened`, so nothing re-arms it and
  the PR sits green and idle for good. `gh pr merge N --auto --merge` puts it
  back (it warns that the queue owns the strategy, and enqueues anyway). Every
  rebase after the PR opens lands here — which is every branch that took a round
  of his feedback; cost an hour on 2026-09-07.
  So wait on `until [ merged ] || [ "$(gh pr view N --json mergeStateStatus
  --jq .mergeStateStatus)" = DIRTY ]` rather than on MERGED alone, and read
  `isInMergeQueue` the first time that wait runs long with nothing changed.

- **Get his eyes on the branch before the PR — leave him a shortcut.** He runs Fun
  Time from the primary checkout, which only moves when `main` does, so a branch
  used to be a change he could not see, run or judge; that is why the rule here
  was once "land it unverified rather than park it". It no longer is. With your
  suites green, run `python -m fun_time.branch_session --shortcut` from your
  worktree: it leaves a `Verify <branch>.lnk` in that worktree and prints the
  path. It goes there rather than in his Fun Time folder so that it is invisible
  to every other agent's end-of-work sweep — in one shared folder each of them
  found launchers that were not theirs and reported them to him (2026-09-18).
  **Never hand him a launcher that opens the branch in VR**, and never
  run `--shortcut --vr` (`~/.claude/hooks/block-vr-verify-shortcut.py` refuses
  it): he checks VR work by opening Fun Time as usual and entering VR from it,
  and a launcher straight into VR puts a layer between the branch and what he
  really runs, so what it shows may not be what he uses (his call, 2026-09-12).
  A branch that changes FunTimeVR gets the same desktop launcher. Then hand him that `.lnk` as a **claunch launch link**
  (the global CLAUDE.md's launcher law), the path percent-encoded —
  `[▶ Launch the Fun Time preview](http://127.0.0.1:41777/launch?t=a780245a4cdcfcb2a2e3b365&p=C%3A%5CUsers%5C<you>%5Cworkspace%5Chaglio%5Cfun_time%5C.claude%5Cworktrees%5Cmy-branch%5CVerify%20my-branch.lnk)`
  (the `--shortcut` run prints the real path — encode that, not this shape)
  — so one click launches it. A `file:///` link only previews the file in the
  app's viewer, so it launches nothing (2026-09-06); linking the folder and naming the file
  spends a window, a scroll and a second click of his on nothing; he had to
  correct two agents in a row for it (2026-07-29). **Never hand him a branch to
  choose or a command to run** — picking is your job, not his. Double-clicking it runs a real session on
  your worktree's code (his real library, his real monitors, uncommitted edits
  included), replacing the live session rather than joining it;
  `fun_time/branch_session.py` says what it isolates and what it shares on
  purpose. **Say nothing about what he does once he is finished with it** —
  quitting it leaves nothing running and nothing to put back, so "then launch
  Fun Time the usual way" only made him stop and ask what that step was for
  (2026-09-18). Then on his word: PR → queue → `git -C <primary> pull --ff-only origin
  main` → `python -m fun_time.branch_session --remove-shortcut` from your
  worktree, **before** you tell him it is done (the worktree itself is his to
  retire, not yours — see the global law, which also forbids mentioning it).
  **Always take your shortcut back out.** The work is in Fun Time by then,
  so a file still offering to run it separately is clutter he has to reason
  about, and nothing else sweeps it until some other agent happens to write one.
  Then tell him it is live the next time he opens Fun Time — not that it
  "needs a restart", which he only has running while he is using it, unlike
  the broker's tray and Evolver. Only he may waive the launch —
  "just land it" is his call to make, never yours. **The shortcut is owed to him
  whether or not you are the one landing** — an agent handing its branch to
  another agent to merge still gets his verdict first, because the merge is what
  the verdict decides. Spawning one, say that outright in the brief: told only
  "do not land anything" and "do not run the app on his screen" (which means
  never open a window on his display yourself, not "skip the verify"), four
  spawned agents in a row read the whole handoff as belonging to the merger and
  gave him nothing to click (2026-08-19).

- **A new config key your branch reads must also go in his real config, or that
  session will not see it.** `launch_branch.vbs` builds the branch config with
  the PRIMARY checkout's `branch_session` — main's code, by design — so a key
  main's `_pin_paths_to_the_primary` has never heard of is never written, your
  loader silently falls back to its default, and he watches the bug you just
  fixed happen again. Unknown keys ARE copied through verbatim from
  `fun_time_config.json`, so add it there, absolute (main will not resolve a
  relative one for you), before handing him the shortcut. Cost the broker/OSR2
  indicator fix a whole round trip on 2026-07-28.

- **Which checkout of `../genau` your session runs is NOT one of those — say it
  in your worktree, never in his config.** `paths.genau_project_dirs` looks like
  an ordinary key, but it is per session: written into his
  `fun_time_config.json` it repoints his ordinary `launch.vbs` session and every
  other agent's branch session at your unlanded genau branch, for as long as it
  sits there, and nothing sweeps it back out. Write
  `state/genau_project_dirs.txt` in your own worktree instead — one absolute path
  per line, and an **empty** file when your branch has nothing to do with genau,
  which is how you get the plain install even if his config is pinned to someone
  else's genau worktree. Cost the main-player minimize button a round trip on
  2026-08-13: it had landed in genau, and the pinned checkout predated it, so it
  was on neither the demo nor his app.

- **That chain has no error state, so prove it at handoff time and re-prove it
  before ever suspecting your code.** An override that is missing, empty, or
  naming a stale checkout silently runs some other genau: the session comes up
  fine, and he sits watching code that is not your branch believing he is
  judging it — a whole verification round demoed nothing on 2026-08-13.
  `--shortcut` prints the checkouts the next launch will carry, read exactly
  the way the launcher reads them (his config, then your worktree's override) —
  read that line every time you hand him the shortcut. When he reports "no
  difference", check delivery before code: the worktree's
  `state/fun_time_branch_config.json` records what his last launch actually
  ran.

- **The Main Player runs out of Genau's venv, so a change here that makes
  `main_player` import a new player_core name needs Genau's pin moved first.**
  `FunTime-MainPlayer.exe` is `../genau/.venv`'s, and that venv holds the
  player_core Genau's `pyproject.toml` names, not this repo's; the satellites
  and the orchestrator run out of this venv. Moving this repo's pin alone
  leaves the Main Player dying at import on a name its venv does not have,
  which the hidden-desktop suite reports as every session failing to start
  (2026-10-07: `play_points` moved to player_core, the pin moved here, and
  the Main Player could not find it until genau took the same tag). So land
  Genau's pin bump first, reinstall that venv, then this repo's.
- **A player_core worktree named on that chain needs nothing copied in, and a
  launch proves that before it opens a window.** The DLL is fetched rather than
  tracked, so a worktree of that repo has no `vendor/` of its own; every launch
  asks each player's own interpreter, with the PYTHONPATH that player is launched
  with, whether it can load the engine, puts the shared copy
  (`%USERPROFILE%\.haglio\libmpv`) where a player that cannot looks first, and
  refuses to open the room — saying which player and what it said — if one still
  cannot (`fun_time/player_engine.py`). A worktree pinned at a player_core from
  before v0.1.331 still looks under `AppData`, which is private to the app an
  agent's shell runs inside and empty for anything he launches; that is the case
  the copy exists for.

- **An Origenerator worktree named on that chain (`state/origenerator_dir.txt`)
  needs `content.local.json` and `state/ui_state.json` copied in from the
  primary Origenerator's.** Both are git-ignored, so a fresh worktree has
  neither: without the overlay the app cannot find the library and opens an
  empty database of its own, and without the window state nothing is open in
  the gallery. He gets a dummy to judge and has to ask what it is (2026-09-20).
  `--shortcut` refuses until both are there.

Everything else in the global CLAUDE.md — work in a worktree, green tests before
you push, clean handoff — still applies.
