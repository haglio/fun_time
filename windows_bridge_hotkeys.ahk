#Requires AutoHotkey v2.0
#SingleInstance Force
; A persistent script gets a tray icon unless this directive says otherwise, and
; nothing here needs one: Ctrl+Alt+Q and closing the dashboard both end the
; session, and the bridge log is a file on disk. Persistent below is what keeps
; the process alive — the icon never was.
#NoTrayIcon
Persistent

; Minimal AHK hotkey script — launched by the Python orchestrator.
;
; All command dispatch is handled by the Python background dispatch loop.
; Hotkeys queue commands via the dashboard command file; Python processes them.
;
; Args:
;   1  WINDOWS_BRIDGE_MANIFEST_PATH
;   2  PIDS_FILE_PATH  — watched rather than read: the orchestrator writes it
;      the moment the session is up, which is this script's cue to go live.
;   3  The orchestrator's pid — after a crossing this script outlives it.

if (A_Args.Length < 3) {
    MsgBox("Expected 3 arguments: manifest path, pids file path, orchestrator pid. Got " . A_Args.Length, "fun_time", "Iconx")
    ExitApp 2
}

WINDOWS_BRIDGE_MANIFEST_PATH := A_Args[1]
PIDS_FILE_PATH := A_Args[2]
; A handle rather than the bare pid: Windows never hands a pid back out while a
; handle to its process is open, so the watch below cannot mistake a stranger
; for the orchestrator.  SYNCHRONIZE is all it asks for.
ORCHESTRATOR := DllCall("OpenProcess", "UInt", 0x00100000, "Int", false, "UInt", A_Args[3], "Ptr")

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
; The crossing cover's file, and how long it may go unrefreshed before the
; crossing counts as over -- session_handoff's name and timeout, pinned by a test.
CROSSING_PROGRESS_FILE := STATE_DIR . "\crossing_progress.txt"
CROSSING_STALE_S := 20

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
; Its twin at the other end: set when the session ends, never lifted.
global EndingPhase := false

; --- Setup ---

; Suspended for the same stretch, so those keys are not merely dropped but never
; taken: a suspended hotkey passes its key through to whatever does have the
; focus, and during a launch that may well be an app of the user's own.  Esc and
; the quit chord are #SuspendExempt, which is what still lets them call the
; launch off.
Suspend true
global StartupSuspended := true
global PauseHold := false
global HeadsetOff := false

SetTimer(ProcessAhkCommand, 50)
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

Space::QueueCommand("enter_omnipause")
Enter::QueueCommand("voice_accept")
NumpadEnter::QueueCommand("voice_accept")
g::QueueCommand("genau_activate")
; The satellite side's own switch: kino mode <-> Origenerator over the RFB.
x::QueueCommand("satellites_toggle")
$f::QueueCommand("fmode_toggle")
b::QueueCommand("broker_panel")

\::QueueCommand("quarter_button")
-::QueueCommand("main_nudge_prev")
; The + key is the = key: no Shift, the way M is pressed without one.
=::QueueCommand("main_nudge_next")
+=::QueueCommand("main_nudge_next")
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
; The shifted step keys walk the versions of the clip on screen -- the upscale
; the library plays and the original it was made from -- as they do on the Main
; Funestra's own side (+m / +SC034 below).  The map's own row, which they used to walk,
; is still reachable by clicking a thumbnail.
+Left::QueueCommand("portrait_cycle_version_back")
+Right::QueueCommand("portrait_cycle_version")
+a::QueueCommand("landscape_cycle_version_back")
+d::QueueCommand("landscape_cycle_version")
; HUD map keyboard navigation: Shift + up/down moves a selection down the
; portrait map's action column and Shift + W/S down the landscape map's, each
; switching the satellite to the selected clip (like a thumbnail click).  These
; are distinct from the unshifted nav keys above, and are suspended under
; OmniPause like the rest.
+Up::QueueCommand("portrait_nav_up")
+Down::QueueCommand("portrait_nav_down")
+w::QueueCommand("landscape_nav_up")
+s::QueueCommand("landscape_nav_down")
; Where each Funestra's HUD sits.  Ctrl and the side's own step keys move that
; Funestra's HUD: round the four corners of its picture on the desktop, against a
; side of its Funestra in the headset.  The Main Funestra has one pair of keys
; rather than a cluster, so its HUD walks the ring on Ctrl and its own step keys:
; Ctrl+M against the clock, Ctrl+. with it.
^Left::QueueCommand("portrait_hud_left")
^Right::QueueCommand("portrait_hud_right")
^Up::QueueCommand("portrait_hud_up")
^Down::QueueCommand("portrait_hud_down")
^a::QueueCommand("landscape_hud_left")
^d::QueueCommand("landscape_hud_right")
^w::QueueCommand("landscape_hud_up")
^s::QueueCommand("landscape_hud_down")
^m::QueueCommand("main_hud_counterclockwise")
^SC034::QueueCommand("main_hud_clockwise")

; Save clip is the semicolon, bound by scancode because a bare ; opens a comment in AHK.
SC027::QueueCommand("genaumacher_save")

; The Main Funestra loop recording: hold R to mark, release to loop, press again to cancel.
; The held flag suppresses key-repeat so only one RECORD_DOWN is queued.
global RecordHeld := false
r:: {
    global RecordHeld
    if RecordHeld
        return
    RecordHeld := true
    QueueCommand("main_player_record_down")
}
r up:: {
    global RecordHeld
    RecordHeld := false
    QueueCommand("main_player_record_up")
}

; The Main Funestra: cycle to another same-content version of the current video.
; Its shifted step keys (M and the period) walk the same versions either way, as
; the shifted step keys on each satellite side do.
v::QueueCommand("main_player_cycle_version")
+m::QueueCommand("main_player_cycle_version_back")
+SC034::QueueCommand("main_player_cycle_version")

; The Main Funestra: cycle the length of what plays — mixed (everything) / clips / full.
t::QueueCommand("main_player_toggle_length")

; FunTimeVR: cycle the Main Funestra's video's projection.  Desktop Main Funestra ignores the verb.
p::QueueCommand("projection_cycle")
!Left::QueueCommand("projection_narrower")
!Right::QueueCommand("projection_wider")

; Robot Hand hotkeys.  Each pair reads left-down / right-up: 7/9 sit
; above u/o for amplitude, the way u/o and j/l already work for center and speed.
7::QueueCommand("robot_hand_amplitude_down")
9::QueueCommand("robot_hand_amplitude_up")
u::QueueCommand("robot_hand_center_down")
i::QueueCommand("robot_hand_cycle_shape")
o::QueueCommand("robot_hand_center_up")
; …and speed, which names no engine here the way the console's marks do, so it
; follows whichever holds the OSR2 — the video's rate under a driving funscript,
; Genau's motion otherwise.
j::QueueCommand("speed_down")
l::QueueCommand("speed_up")
; Cruise varies the motion; moving on from a flick is what an unlocked Genau does
; by itself, so it is the apostrophe's lock rather than a switch of its own.
c::QueueCommand("robot_hand_toggle_cruise")

; The Main Funestra's cluster, laid out like the arrow keys are for the portrait
; side: M and . either side for previous and next, the comma to hold, each
; reaching whatever is on the Main Funestra -- the video in Kino mode, Genau's
; flick in Genau mode -- and K above the comma to condemn the flick, where Up
; sits over Down and W over S.  Comma and period by scancode, the way the
; semicolon is.
k::QueueCommand("genau_weird_flick")
m::QueueCommand("main_prev")
SC033::QueueCommand("main_lock")
SC034::QueueCommand("main_next")

; -------------------- CORE FUNCTIONS --------------------

; The way out of a session — and, while one is still assembling, the way to
; call it off.  Exiting mid-startup would leave the orchestrator building a
; session it has been told to end and only take that session down once it was
; fully up, and it would take Esc's cancel with it: a script that has exited
; hooks nothing.
EndSession() {
    global StartupPhase, EndingPhase
    if (StartupPhase || EndingPhase) {
        ; Mid-startup this cannot exit: the orchestrator is still building a
        ; session that has to come down first -- and once the session is ending
        ; there is nothing left to end.  The word in the flag is what tells the
        ; two keys apart -- Esc means "put me back", this means "end everything"
        ; -- and the session-end marker cannot, a crossing having already left
        ; one of its own.
        RequestStartupCancel("quit")
        return
    }
    ; Marked because everything the orchestrator sees from here is identical
    ; whether this was asked for or not: the closing screen goes up and the
    ; session comes down with code 0.  Without it a session that died on its
    ; own reads exactly like one the user quit.
    MarkSessionEnd("the quit chord (Ctrl+Alt+Q)")
    EndTheSession()
}

; The note the orchestrator reads to tell an asked-for end from an unexpected
; one.  It removes the file, so a session only ever finds its own.
MarkSessionEnd(reason) {
    global STATE_DIR
    try FileDelete(STATE_DIR . "\session_end.txt")
    AppendWithRetry(reason, STATE_DIR . "\session_end.txt", 3, 50)
}

; The same note, but never over one already there.  Everything that is not the
; quit chord reaches this script through the command channel below, which can
; say no more than that it was asked -- so a spoken "quit", a crossing to the
; headset and the dashboard window's close box all read identically, and "what
; ended that session?" had no answer.  Whatever asked writes its own phrase
; before it asks for the end (fun_time\session_end.py); this keeps it.
KeepOrMarkSessionEnd(reason) {
    global STATE_DIR
    if (FileExist(STATE_DIR . "\session_end.txt"))
        return
    MarkSessionEnd(reason)
}

; The session ends here and the script does not: Esc and the quit chord stay
; live over the closing cover.  The orchestrator stops the script once its
; teardown is done -- except over a crossing, where it goes on hearing Esc for
; the relay until the next session's own script replaces it.
EndTheSession() {
    global EndingPhase, StartupSuspended, STARTUP_CANCEL_FILE
    try FileDelete(STARTUP_CANCEL_FILE)
    EndingPhase := true
    StartupSuspended := false
    ApplyHolds()
    SetTimer(WatchEnding, 500)
}

; Over the closing cover its orchestrator is still running, and over a crossing
; the cover's file is kept fresh; with neither, nothing is left to hear Esc for.
WatchEnding() {
    if (OrchestratorGone() && !CrossingUnderWay()) {
        Log("Nothing left to end or cross into; exiting")
        ExitApp()
    }
}

OrchestratorGone() {
    global ORCHESTRATOR
    ; 0 is WAIT_OBJECT_0, the process having ended; no handle, it was gone already.
    return !ORCHESTRATOR || DllCall("WaitForSingleObject", "Ptr", ORCHESTRATOR, "UInt", 0) = 0
}

CrossingUnderWay() {
    global CROSSING_PROGRESS_FILE, CROSSING_STALE_S
    try {
        if (Trim(FileRead(CROSSING_PROGRESS_FILE, "UTF-8")) = "DONE")
            return false
        return DateDiff(A_Now, FileGetTime(CROSSING_PROGRESS_FILE, "M"), "Seconds") < CROSSING_STALE_S
    } catch {
        return false
    }
}

; Esc calls the launch off while the session is still assembling, and the end
; off once it is ending; in between it pauses the session.
PauseOrCancelStartup() {
    global StartupPhase, EndingPhase
    if (StartupPhase || EndingPhase) {
        RequestStartupCancel("cancel")
        return
    }
    QueueCommand("omnipause_toggle")
}

RequestStartupCancel(reason) {
    global STARTUP_CANCEL_FILE
    ; The cover stays up until the orchestrator has torn down whatever it had
    ; launched, so nothing half-started is ever revealed.  *reason* is "cancel"
    ; or "quit"; a second press costs one more line, and any "quit" among them
    ; is what the orchestrator reads.
    if AppendWithRetry(reason . "`n", STARTUP_CANCEL_FILE)
        Log("Startup cancel requested (" . reason . ")")
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
    StartupSuspended := false
    ApplyHolds()
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

AppendWithRetry(text, path, attempts := 5, delayMs := 5, access := "exclusive") {
    ; FileAppend past transient Windows sharing violations (error 32) that occur
    ; when another process briefly holds the file. Returns true once written.
    ; "append-only" is for a file another process appends to as well: FileAppend
    ; takes the file exclusively, and a plain shared append can land on the
    ; offset the other writer just filled.  A handle with FILE_APPEND_DATA and
    ; no FILE_WRITE_DATA writes at the end of the file every time.
    loop attempts {
        try {
            if (access = "append-only") {
                handle := DllCall("CreateFileW", "Str", path, "UInt", 0x0004 | 0x00100000,
                                  "UInt", 7, "Ptr", 0, "UInt", 4, "UInt", 0x80, "Ptr", 0, "Ptr")
                if (handle = -1)
                    throw OSError(A_LastError)
                target := FileOpen(handle, "h", "UTF-8-RAW")
                target.Write(text)
                target.Close()
                DllCall("CloseHandle", "Ptr", handle)
            } else {
                FileAppend(text, path, "UTF-8-RAW")
            }
            return true
        }
        Sleep(delayMs)
    }
    return false
}

ProcessAhkCommand() {
    global AHK_CMD_FILE, PauseHold, HeadsetOff, StartupPhase, EndingPhase
    if !FileExist(AHK_CMD_FILE)
        return
    taken := AHK_CMD_FILE . ".taken"
    try {
        FileMove(AHK_CMD_FILE, taken, 1)
        actions := FileRead(taken, "UTF-8")
        FileDelete(taken)
    } catch {
        return
    }
    Loop Parse, actions, "`n", "`r `t" {
        action := A_LoopField
        if (action = "suspend_hotkeys") {
            PauseHold := true
        } else if (action = "unsuspend_hotkeys") {
            PauseHold := false
        } else if (action = "headset_off") {
            HeadsetOff := true
        } else if (action = "headset_on") {
            HeadsetOff := false
        } else if (action = "end_session") {
            KeepOrMarkSessionEnd("an end asked on the AHK command channel")
            EndTheSession()
        } else if (action = "exit") {
            if (!StartupPhase && !EndingPhase)
                KeepOrMarkSessionEnd("an exit on the AHK command channel")
            ExitApp()
        }
    }
    ApplyHolds()
}

ApplyHolds() {
    global StartupSuspended, PauseHold, HeadsetOff, EndingPhase
    Suspend(StartupSuspended || PauseHold || HeadsetOff || EndingPhase)
    ways_out := (HeadsetOff && !EndingPhase) ? "Off" : "On"
    Hotkey("Esc", ways_out)
    Hotkey("+Esc", ways_out)
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
    AppendWithRetry(line, WINDOWS_BRIDGE_LOG_FILE, 3, 50, "append-only")
}
