# OSR2 Idle Monitor — Prior Attempt Notes (2026-03-28)

A previous agent attempted to build this feature and failed. This document captures what was learned so the next attempt doesn't repeat the same mistakes.

## The user's problem

"I keep leaving my OSR2 powered on all night after using it." They want:

1. **Idle alert:** If the OSR2 has been idle for 15 minutes, show a Windows message box.
2. **Shutdown block:** If shutting down with the OSR2 still on, block shutdown and show a warning (using `ShutdownBlockReasonCreate` + returning FALSE from `WM_QUERYENDSESSION`).

## Hardware facts (verified)

- The OSR2 connects via USB-serial (COM4).
- The USB cable is **always physically connected**, so **COM4 is always enumerated** regardless of whether the OSR2 is powered on or off. Checking COM port presence is useless.
- The OSR2 has a physical power switch. "On" means the firmware is running.

## Broker architecture (verified by reading code and logs)

- `broker_app.py` runs at Windows startup via `launch_broker_tray.vbs`.
- It opens a serial session: virtual port (COM8/COM15) <-> real port (COM4).
- `broker_heartbeat.txt` is written every 0.5s **unconditionally** (even if the OSR2 is off). Heartbeat freshness tells you the broker process is alive, NOT that the device is on.
- `forward_real_to_virtual`: reads data FROM the OSR2 (COM4) and writes to the virtual port.
- `forward_virtual_to_real`: reads data FROM the virtual port (MFP TCode commands) and writes to the OSR2. This path is **skipped** when `auto_mode.is_active` is True.
- `BrokerAutoController.handle_line()` parses lines from the OSR2 looking for "tcode task started", "is on", BPM data, stroke data. These set `auto_mode.is_active`.

## What data actually flows (verified from broker.log and genau_listener.log)

### MFP-controlled content
When MultiFunPlayer plays a video with a funscript, it sends TCode commands to the virtual COM port. These flow through `forward_virtual_to_real` to COM4. **This is reliable and observable.**

### OSR2 built-in free mode ("auto mode on the OSR2")
The OSR2's firmware generates motion internally. The only PC-visible signal is the **initial status message** ("tcode task started", "free mode is on") sent via serial when:
- The device first powers on, OR
- The user activates free mode via the device's own controls

**Critical finding:** These messages are sent **once** on initial connection. If the broker is restarted while the OSR2 is already running, the messages are NOT resent. In testing, the broker log showed AUTO ON/OFF entries only in one early session (06:19). All later sessions (after broker restarts) showed zero AUTO entries and zero serial data from the device. The activity file was never updated.

**The OSR2 does NOT send periodic data during free mode operation.** It sends status messages at state transitions only, and only if the serial connection was freshly established at that moment.

### Genau auto mode (broker-controlled)
The broker's `BrokerAutoController` sends UDP to a Genau listener process. Genau generates TCode clips. Genau does NOT write to any serial port directly (confirmed by grepping all Python files — only broker files reference serial). How Genau's TCode reaches the OSR2 is unclear — possibly via AHK or the virtual port, but when `auto_mode.is_active` is True, `forward_virtual_to_real` skips `real.write`.

## What was attempted and why it failed

### Approach: Activity file written by broker on serial data flow
Added `_maybe_write_activity()` (writes timestamp to `broker_activity.txt`, throttled to 10s) called from:
- `forward_real_to_virtual` (data FROM device)
- `forward_virtual_to_real` (data TO device, added later)

A separate `osr2_monitor` project read this file and used a state machine (OFF / WAITING / ACTIVE / IDLE) to decide when to alert.

**Why it failed:**
- The OSR2's built-in free mode generates no broker-visible serial data after the initial connection message.
- The initial message is only sent on fresh connection, not on subsequent free mode activations.
- Result: `broker_activity.txt` was never updated during the user's testing, so the monitor stayed in WAITING forever.

### State machine issues encountered along the way
- **False positives without WAITING state:** Broker heartbeat is always fresh, so without WAITING, the monitor immediately started an idle timer at broker startup and alerted even when the OSR2 was off.
- **Double-counting idle_threshold:** `_is_activity_fresh()` used `idle_threshold` as the freshness window, AND the idle timer also ran for `idle_threshold`. Total time to alert was 2x the intended threshold. Fixed by setting `_idle_since` to the activity file's timestamp instead of `now`.
- **Infinite re-alerting after device turned off:** After alert fired, re-arming the timer caused alerts every 30 seconds forever (can't distinguish "device off" from "device on but idle"). Fixed by going to WAITING after alert dismissal (requires new activity before next alert).

### Shutdown blocking (partially built, never fully tested)
- `MessageBoxW` in the wndproc blocks the return, causing Windows to time out and shut down anyway. Use `ShutdownBlockReasonCreate` instead — it returns immediately and lets Windows show its own blocking dialog.
- The message pump for `WM_QUERYENDSESSION` must run on the **main thread** (not a daemon thread), otherwise it gets killed before it can handle the message.
- `ctypes.wintypes` does NOT include `WNDCLASSW` — must define it as a custom `ctypes.Structure`.
- Must declare `argtypes` for all Win32 calls involving handles (`HINSTANCE`, `HWND`) on 64-bit Python, or you get `OverflowError: int too long to convert`.

## What the next attempt should consider

1. **The MFP path works.** TCode commands from MultiFunPlayer flow through the broker's `forward_virtual_to_real` reliably. Writing activity there is viable for detecting "was playing content, then stopped."

2. **OSR2 built-in free mode is not detectable** from the PC without either:
   - Modifying the OSR2 firmware to send periodic heartbeats
   - Having the broker send a probe command and checking for a response (TCode has no ACK protocol, so this may not work)
   - Finding another signal (DTR/DSR/CTS lines, USB power state, etc.)

3. **The user's real-world scenario** is most likely "was watching content via MFP, stopped, forgot to turn off the device." This is the MFP path and should be solvable.

4. **Check the actual data flow FIRST** before writing any code. Read `broker.log`, check `broker_activity.txt` timestamps, verify your assumptions about what signals exist. The previous agent wrote multiple rounds of code based on wrong assumptions about OSR2 serial behavior.

5. **The user finds the OSR2's built-in free mode important for testing.** Any solution that only works for MFP content may be unsatisfying if it can't also handle free mode. Understand this constraint before proposing a design.

6. **`MB_SETFOREGROUND` (0x00010000)** flag on `MessageBoxW` brings the dialog to the front. Without it, the alert can appear behind other windows.
