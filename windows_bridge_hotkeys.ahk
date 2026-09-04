#Requires AutoHotkey v2.0
#SingleInstance Force
; A persistent script gets a tray icon unless this directive says otherwise, and
; nothing here needs one: Ctrl+Alt+Q and closing the dashboard both end the
; session, and the bridge log is a file on disk. Persistent below is what keeps
; the process alive — the icon never was.
#NoTrayIcon
Persistent
DetectHiddenWindows False
SetTitleMatchMode 2

; Minimal AHK hotkey script — launched by the Python orchestrator.
;
; All command dispatch is handled by the Python background dispatch loop.
; Hotkeys queue commands via the dashboard command file; Python processes them.
;
; Args:
;   1  WINDOWS_BRIDGE_MANIFEST_PATH
;   2  PIDS_FILE_PATH  — watched rather than read: the orchestrator writes it
;      the moment the session is up, which is this script's cue to go live.

if (A_Args.Length < 2) {
    MsgBox("Expected 2 arguments: manifest path, pids file path. Got " . A_Args.Length, "fun_time", "Iconx")
    ExitApp 2
}

WINDOWS_BRIDGE_MANIFEST_PATH := A_Args[1]
PIDS_FILE_PATH := A_Args[2]

; Read only the values that the hotkey script needs.
DASHBOARD_CMD_FILE := RequireManifestValue("commands", "dashboard_cmd_file")
WINDOWS_BRIDGE_LOG_FILE := RequireManifestValue("runtime", "windows_bridge_log_file")
STATE_DIR := GetParentDir(WINDOWS_BRIDGE_LOG_FILE)

AHK_CMD_FILE := STATE_DIR . "\ahk_cmd.txt"
; Dropping this flag is how a launch gets called off: the orchestrator's next
; progress checkpoint sees it and unwinds startup.  The name has to match
; overlay_progress.CANCEL_FILENAME — the loading screen drops the same file
; when Esc happens to land on it — so a test pins the two together.
STARTUP_CANCEL_FILE := STATE_DIR . "\startup_cancel.flag"

; This script goes up with the loading screen, ahead of every window the session
; opens, because its hotkeys are the only keys here that do not care what holds
; the focus: AHK hooks the keyboard rather than waiting its turn in a window's
; message queue.  The loading screen's own Esc binding cannot do that, so
; anything that takes the focus mid-launch leaves the launch uncancellable —
; which is the failure this ordering is written against.
;
; Up that early, though, the keys that drive a session have nothing to drive:
; they would queue commands into a file no dispatch loop is draining yet.  So
; while StartupPhase holds, QueueCommand drops them, and the two keys that mean
; "stop" ask startup to unwind instead of doing their session jobs.  WatchStartup
; lifts it.
global StartupPhase := true

; --- Setup ---

; Suspended for the same stretch, so those keys are not merely dropped but never
; taken: a suspended hotkey passes its key through to whatever does have the
; focus, and during a launch that may well be an app of the user's own.  Esc and
; the quit chord are #SuspendExempt, which is what still lets them call the
; launch off.  The flag records that this hold is ours to release — once anything
; else has set the suspend state (an integration run's pre-write, or OmniPause),
; the handover must not undo their decision.
Suspend true
global StartupSuspended := true

SetTimer(ProcessAhkCommand, 150)
SetTimer(WatchStartup, 150)

; Liveness beacon: a periodic line proving the hotkey script's message pump is
; still running. If it stops (then resumes after a gap), AHK froze — e.g. the
; machine slept. If it keeps ticking while keys stop reaching Python, the
; hotkeys/hook died while the process stayed alive. Diagnostic for the
; resume-after-idle failure; pair with the dispatch loop's wake warning.
SetTimer(Heartbeat, 60000)

Log("Hotkey script started")

; -------------------- HOTKEYS --------------------

; Exempt from the wholesale Suspend that omnipause applies: the way out
; (Esc), the way to quit (Ctrl+Alt+Q), and the sensation emergency (Shift+Esc),
; which has to work from INSIDE omnipause — a paused session can still have the
; device on the user.  Shift+Esc is a hotkey in its own right: an unprefixed
; hotkey does not fire while an extra modifier is held, so Esc and +Esc never
; shadow each other (as Left/+Left and a/+a already do below).
#SuspendExempt true
^!q::EndSession()
Esc::PauseOrCancelStartup()
+Esc::QueueCommand("relief_omnipause")
#SuspendExempt false

; The hosted Origenerator's MAIN window is a typing app — prompts, filters,
; renames — and these hotkeys are single bare letters, so while it is focused
; the keyboard is its, wholesale.  Its region SHOWS are not: a slideshow has
; no text field, and the arrows and WASD must drive the portrait and
; landscape regions by SIDE, exactly as they drive the players — wherever the
; focus sits, a show's included.  So only the main window gates the hotkeys
; off.  Matched by EXACT title, not the script's substring mode:
; "Origenerator" appears in plenty of his other windows — an Explorer at the
; checkout, a terminal on a branch — and a substring match silently killed
; every hotkey while one of those was focused.  The exempt trio above stays
; global on purpose — quitting and the omnipause pair are session gestures,
; wherever the focus sits.
OrigeneratorHasKeyboard() {
    title := WinGetTitle("A")
    return (title = "Origenerator")
}
#HotIf !OrigeneratorHasKeyboard()

Space::QueueCommand("enter_omnipause")
[::QueueCommand("main_prev")
SC01A::QueueCommand("main_prev")
]::QueueCommand("main_next")
SC01B::QueueCommand("main_next")
; Mode activation hotkeys: the main slot's two modes.
g::QueueCommand("genau_activate")
h::QueueCommand("main_video_activate")
; The satellite side's own switch: video mode <-> Origenerator over the RFB.
x::QueueCommand("satellites_toggle")
$f::QueueCommand("fmode_toggle")
b::QueueCommand("broker_panel")

\::QueueCommand("quarter_button")
; Nau's library browser, on the key the retired nau mode had.
n::QueueCommand("browse_library")
-::QueueCommand("main_nudge_prev")
=::QueueCommand("main_nudge_next")
Left::QueueCommand("portrait_prev")
Right::QueueCommand("portrait_next")
Up::QueueCommand("portrait_trash")
Down::QueueCommand("portrait_lock")
; Step portrait's loop on: seed family, then action group, then off (back to its
; browse, filter kept), then round again.  This and landscape's E below are the
; only way into or out of a group loop at the keyboard.  E was cycle action until
; cycling a clip's action and seed went spoken-only on both sides — those two held
; Del/End here and E/Q there, and no one ever reached for them.
Home::QueueCommand("portrait_loop")
a::QueueCommand("landscape_prev")
d::QueueCommand("landscape_next")
w::QueueCommand("landscape_trash")
s::QueueCommand("landscape_lock")
; Landscape's half of the loop cycle above.
e::QueueCommand("landscape_loop")
; HUD map keyboard navigation: Shift + arrows move a selection around the
; portrait map and Shift + WASD around the landscape map, each switching the
; satellite to the selected clip (like a thumbnail click).  These are distinct
; from the unshifted nav keys above, and are suspended under OmniPause like the
; rest.  Enter used to lock the selection and re-home the map on it; the side's
; own lock key does both, so the extra key was retired.
+Left::QueueCommand("portrait_nav_left")
+Right::QueueCommand("portrait_nav_right")
+Up::QueueCommand("portrait_nav_up")
+Down::QueueCommand("portrait_nav_down")
+a::QueueCommand("landscape_nav_left")
+d::QueueCommand("landscape_nav_right")
+w::QueueCommand("landscape_nav_up")
+s::QueueCommand("landscape_nav_down")
; The main slot's lock, reaching whichever player is showing: Nau's video
; holds instead of walking the playlist, Genau's clip holds instead of moving on
; every few seconds.  The apostrophe sits beside the satellites' own lock keys on
; the home row, and gave up Save clip to take it — that moved one key left, to the
; semicolon (bound by scancode because a bare ; opens a comment in AHK).
'::QueueCommand("main_lock")
SC027::QueueCommand("clipper_save")

; Nau loop recording: hold R to mark, release to loop, press again to cancel.
; The held flag suppresses key-repeat so only one RECORD_DOWN is queued.
global RecordHeld := false
r:: {
    global RecordHeld
    if RecordHeld
        return
    RecordHeld := true
    QueueCommand("nau_record_down")
}
r up:: {
    global RecordHeld
    RecordHeld := false
    QueueCommand("nau_record_up")
}

; Nau: cycle to another same-content version of the current video.
v::QueueCommand("nau_cycle_version")

; Nau: cycle the length of what plays — mixed (everything) / shorts / full-length.
t::QueueCommand("nau_toggle_length")

; FunTimeVR: cycle the main player's video's projection (flat / 180 / fisheye /
; MKX200 / 360), remembered per video.  Desktop Nau ignores the verb.
p::QueueCommand("projection_cycle")

; FunTimeVR: re-zero the scene onto wherever the headset is facing now (the
; runtime's own recenter menu doesn't reach this app).  Desktop Nau ignores it.
z::QueueCommand("recenter_view")

; FunTimeVR: tilt the whole arrangement up and down, for watching from a
; recliner or flat on your back; Shift+Z stands it upright again beside Z's
; recenter.  The headset's right thumbstick does the same thing continuously —
; these are for the desk.  Desktop Nau ignores all three.
PgUp::QueueCommand("tilt_up")
PgDn::QueueCommand("tilt_down")
+z::QueueCommand("tilt_reset")

; Robot Hand hotkeys.  Each pair reads left-down / right-up: 7/9 sit
; above u/o for amplitude, the way u/o and j/l already work for center and speed.
7::QueueCommand("robot_hand_amplitude_down")
9::QueueCommand("robot_hand_amplitude_up")
u::QueueCommand("robot_hand_center_down")
i::QueueCommand("robot_hand_cycle_shape")
o::QueueCommand("robot_hand_center_up")
; …and speed, which names no engine here the way the console's marks do, so it
; follows whichever holds the OSR2 — the video's rate under a driving funscript,
; Genau's stroke otherwise.
j::QueueCommand("speed_down")
l::QueueCommand("speed_up")
; Cruise varies the stroke; moving on from a clip is what an unlocked Genau does
; by itself, so it is the apostrophe's lock rather than a switch of its own.
c::QueueCommand("robot_hand_toggle_cruise")
SC035::QueueCommand("genau_toggle_auto")

; Genau's clip cluster, laid out like the arrow keys are for the portrait side:
; K above to condemn the clip, M and . either side for previous and next.  The
; hold that used to sit below K is the apostrophe now — one lock key for whichever
; player is on the main slot.
k::QueueCommand("genau_weird_clip")
m::QueueCommand("genau_prev_clip")
SC034::QueueCommand("genau_next_clip")

#HotIf

; -------------------- CORE FUNCTIONS --------------------

; The way out of a session — and, while one is still assembling, the way to
; call it off.  Exiting mid-startup would leave the orchestrator building a
; session it has been told to end and only take that session down once it was
; fully up, and it would take Esc's cancel with it: a script that has exited
; hooks nothing.
EndSession() {
    global StartupPhase
    if (StartupPhase) {
        RequestStartupCancel()
        return
    }
    ; Say so before going, because everything the orchestrator sees from here
    ; is identical whether this was asked for or not: the script exits, the
    ; closing screen goes up, the session comes down with code 0.  Without the
    ; marker a session that died on its own reads in the log exactly like one
    ; the user quit, which is why "it crashed" could not be checked at all.
    MarkSessionEnd("the quit chord (Ctrl+Alt+Q)")
    ExitApp()
}

; The note the orchestrator reads to tell an asked-for end from an unexpected
; one.  It removes the file, so a session only ever finds its own.
MarkSessionEnd(reason) {
    global STATE_DIR
    try FileDelete(STATE_DIR . "\session_end.txt")
    AppendWithRetry(reason, STATE_DIR . "\session_end.txt", 3, 50)
}

; Esc calls the launch off while the session is still assembling, and pauses it
; once it is up.
PauseOrCancelStartup() {
    global StartupPhase
    if (StartupPhase) {
        RequestStartupCancel()
        return
    }
    QueueCommand("omnipause_toggle")
}

RequestStartupCancel() {
    global STARTUP_CANCEL_FILE
    ; The cover stays up until the orchestrator has torn down whatever it had
    ; launched, so nothing half-started is ever revealed.  A second press costs
    ; one more line in a file that is only ever tested for existence.
    if AppendWithRetry("cancel`n", STARTUP_CANCEL_FILE)
        Log("Startup cancel requested")
    else
        Log("Could not drop the startup cancel flag")
}

; The orchestrator writes the pids file once the session is up and its windows
; are placed — the moment these hotkeys have something to reach.  Polled rather
; than announced down the command mailbox below: that mailbox is one slot with
; several writers, and a handover lost there would leave every hotkey dead for
; the rest of the session.
WatchStartup() {
    global StartupPhase, StartupSuspended, PIDS_FILE_PATH
    if !FileExist(PIDS_FILE_PATH)
        return
    StartupPhase := false
    if (StartupSuspended) {
        StartupSuspended := false
        Suspend false
    }
    SetTimer(WatchStartup, 0)
    Log("Session up; startup hold released")
}

QueueCommand(cmd) {
    global DASHBOARD_CMD_FILE, StartupPhase
    if (StartupPhase) {
        ; No session to drive yet, and no dispatch loop draining the file this
        ; would go in.  The keys that mean "stop" never come through here.
        Log("Dropped while starting up: " . cmd)
        return
    }
    ; The Python dispatch loop drains this file by renaming it (~20 Hz). A held
    ; key appends fast enough to overlap that rename, and Windows then refuses
    ; the open with "(32) ... being used by another process". Retry briefly so a
    ; transient collision drops at most one keypress instead of crashing the
    ; hotkey script with an unhandled FileAppend error.
    if !AppendWithRetry(cmd . "`n", DASHBOARD_CMD_FILE)
        Log("QueueCommand dropped (file busy): " . cmd)
}

AppendWithRetry(text, path, attempts := 5, delayMs := 5) {
    ; FileAppend past transient Windows sharing violations (error 32) that occur
    ; when another process briefly holds the file. Returns true once written.
    loop attempts {
        try {
            FileAppend(text, path, "UTF-8-RAW")
            return true
        }
        Sleep(delayMs)
    }
    return false
}

ProcessAhkCommand() {
    global AHK_CMD_FILE, StartupSuspended
    if !FileExist(AHK_CMD_FILE)
        return
    try {
        action := Trim(FileRead(AHK_CMD_FILE, "UTF-8"))
        FileDelete(AHK_CMD_FILE)
    } catch {
        return
    }
    if (action = "")
        return
    if (action = "suspend_hotkeys") {
        Suspend true
        StartupSuspended := false
    } else if (action = "unsuspend_hotkeys") {
        Suspend false
        StartupSuspended := false
    } else if (action = "exit") {
        MarkSessionEnd("an exit on the AHK command channel")
        ExitApp()
    }
}

Heartbeat() {
    Log("AHK heartbeat (suspended=" . A_IsSuspended . ")")
}

; -------------------- UTILITIES --------------------

GetParentDir(path) {
    SplitPath(path, , &dirPath)
    return dirPath
}

RequireManifestValue(section, key) {
    global WINDOWS_BRIDGE_MANIFEST_PATH
    missing := "__missing__"
    value := IniRead(WINDOWS_BRIDGE_MANIFEST_PATH, section, key, missing)
    if (value = missing) {
        MsgBox("Missing windows bridge manifest value [" . section . "] " . key, "fun_time", "Iconx")
        ExitApp 2
    }
    return value
}

Log(msg) {
    global WINDOWS_BRIDGE_LOG_FILE
    line := FormatTime(, "yyyy-MM-dd HH:mm:ss") . " " . msg . "`r`n"
    AppendWithRetry(line, WINDOWS_BRIDGE_LOG_FILE, 3, 50)
}
