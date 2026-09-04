# Why a VR Session Now Quits the Pimax Runtime

**Symptom (2026-09-03):** after FunTimeVR or GenauVR ended, the headset stayed
powered on. Turning it off meant starting Pimax Play by hand purely so it could
be quit. Other VR apps did not leave it that way — the user starts Pimax Play
himself for those, sees it, and quits it.

## What actually holds the headset on

`vr_runtime.ensure_ready()` starts the runtime's own client
(`PimaxClient.exe`) when nothing else has, and starts it **hidden**
(`hidden_subprocess_kwargs`). The client then brings up the services that do
the work, and *those* outlive it. From `%LOCALAPPDATA%\Pimax\PiService\`, one
session's launcher log:

```
15:38:02  PiPlayService     start   -> PiPlayService.exe        pid 45804
15:38:02  PiPlatformService start   -> PiPlatformService_64.exe pid 43732
...
16:34:58  PiPlatformService quit    -> force TaskKillOne pid 43732
16:34:58  PiPlayService     quit    -> SendQuitEvent: PvrServerQuitEvent
16:35:01                               pi_server.exe exit with exit code: 0
```

The same two PIDs span 15:38 to 16:35 — through that VR session, through a
second one at 16:00, and for half an hour after the last one ended. The
16:34 entries are the user starting Pimax Play and quitting it. `pi_server.exe`
drives the headset's displays, and its exit at 16:35:01 is the headset going
off.

So nothing was wrong with the OpenXR teardown: the runtime we started simply
had no one left to quit it, and nothing on screen to quit it with.

## What the fix runs

Pimax publishes no CLI for this. `_QUIT_SERVICES` are the commands its own
client logs itself running on Exit, through `Runtime\launcher.exe`, in that
order — `PiPlatformService` then `PiPlayService`, whose quit signals
`pi_server` and blocks until it has gone. The client is killed first because
it is the one thing that would start the services straight back up.

A Pimax update that renamed a service would cost a headset left on, not a
broken session: every step is best-effort and bounded by `QUIT_TIMEOUT_S`.

## What it will not touch

`runtime_was_running()` is read once, before the session's first
`ensure_ready()`, and off `pi_server.exe` rather than off the client — the
client is a window onto services that outlive it, so a session reading the
client could mistake an already-lit headset for one of its own. A runtime that
was already up is the user's and is left alone.

In FunTimeVR the reading and the quit both live in `fun_time_vr.orchestrator`,
not in the player: the orchestrator kills the player outright at session end,
so nothing in the player's own teardown can be relied on to run. GenauVR's app
is one process that exits normally, so it does both itself.
