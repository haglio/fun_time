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
;   2  PIDS_FILE_PATH  (accepted for compatibility, no longer read)

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

; --- Setup ---

SetTimer(ProcessAhkCommand, 150)

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
^!q::ExitApp()
Esc::QueueCommand("omnipause_toggle")
+Esc::QueueCommand("relief_omnipause")
#SuspendExempt false

; The hosted Origenerator is a typing app — prompts, filters, renames — and
; these hotkeys are single bare letters.  While any of its windows is focused
; (the main one over the RFB, or a show on a satellite region) the keyboard is
; its, wholesale: a show handles its own arrows, and a prompt can contain
; every letter bound below.  Matched by EXACT title, not the script's
; substring mode: "Origenerator" appears in plenty of his other windows — an
; Explorer at the checkout, a terminal on a branch — and a substring match
; silently killed every hotkey while one of those was focused.  The exempt
; trio above stays global on purpose — quitting and the omnipause pair are
; session gestures, wherever the focus sits.
OrigeneratorHasKeyboard() {
    title := WinGetTitle("A")
    return (title = "Origenerator" || title = "Origenerator Portrait"
        || title = "Origenerator Landscape")
}
#HotIf !OrigeneratorHasKeyboard()

Space::QueueCommand("enter_omnipause")
[::QueueCommand("main_prev")
SC01A::QueueCommand("main_prev")
]::QueueCommand("main_next")
SC01B::QueueCommand("main_next")
; Mode activation hotkeys
g::QueueCommand("genau_activate")
h::QueueCommand("hybrid_activate")
n::QueueCommand("nau_activate")
; The satellite side's own switch: player mode <-> Origenerator over the RFB.
x::QueueCommand("satellites_toggle")
$f::QueueCommand("fmode_toggle")
b::QueueCommand("broker_panel")

\::QueueCommand("backslash_key")
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

; Genau direct control hotkeys.  Each pair reads left-down / right-up: 7/9 sit
; above u/o for amplitude, the way u/o and j/l already work for center and speed.
7::QueueCommand("genau_amplitude_down")
9::QueueCommand("genau_amplitude_up")
u::QueueCommand("genau_center_down")
i::QueueCommand("genau_cycle_shape")
o::QueueCommand("genau_center_up")
; …and speed, which names no engine here the way the console's marks do, so it
; follows whichever holds the OSR2 — the video's rate under a driving funscript,
; Genau's stroke otherwise.
j::QueueCommand("speed_down")
l::QueueCommand("speed_up")
; Cruise varies the stroke; moving on from a clip is what an unlocked Genau does
; by itself, so it is the apostrophe's lock rather than a switch of its own.
c::QueueCommand("genau_toggle_cruise")
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

QueueCommand(cmd) {
    global DASHBOARD_CMD_FILE
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
    global AHK_CMD_FILE
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
    } else if (action = "unsuspend_hotkeys") {
        Suspend false
    } else if (action = "exit") {
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
