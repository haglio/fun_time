#Requires AutoHotkey v2.0
#SingleInstance Force
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
PROJECT_DIR := RequireManifestValue("runtime", "project_dir")
WINDOWS_BRIDGE_LOG_FILE := RequireManifestValue("runtime", "windows_bridge_log_file")
STATE_DIR := GetParentDir(WINDOWS_BRIDGE_LOG_FILE)
ICON_PATH := PROJECT_DIR . "\icon.ico"

AHK_CMD_FILE := STATE_DIR . "\ahk_cmd.txt"

; --- Setup ---

if FileExist(ICON_PATH)
    TraySetIcon(ICON_PATH)

A_IconTip := "Fun Time Windows Bridge"
A_TrayMenu.Delete()
A_TrayMenu.Add("Open Windows Bridge Log", ShowWindowsBridgeLog)
A_TrayMenu.Add()
A_TrayMenu.Add("Exit Fun Time", (*) => ExitApp())
A_TrayMenu.AddStandard()

SetTimer(ProcessAhkCommand, 150)

Log("Hotkey script started")

; -------------------- HOTKEYS --------------------

#SuspendExempt true
^!q::ExitApp()
Esc::QueueCommand("omnipause_toggle")
#SuspendExempt false

Space::QueueCommand("enter_omnipause")
[::QueueCommand("primary_prev")
SC01A::QueueCommand("primary_prev")
]::QueueCommand("primary_next")
SC01B::QueueCommand("primary_next")
; Mode activation hotkeys
g::QueueCommand("genau_activate")
h::QueueCommand("hybrid_activate")
n::QueueCommand("nau_activate")
$f::QueueCommand("fmode_toggle")
b::QueueCommand("broker_panel")
Backspace::QueueCommand("voice_toggle")

\::QueueCommand("backslash_key")
-::QueueCommand("primary_nudge_prev")
=::QueueCommand("primary_nudge_next")
Left::QueueCommand("portrait_prev")
Right::QueueCommand("portrait_next")
Up::QueueCommand("portrait_trash")
Down::QueueCommand("portrait_lock")
a::QueueCommand("landscape_prev")
d::QueueCommand("landscape_next")
w::QueueCommand("landscape_trash")
s::QueueCommand("landscape_lock")
'::QueueCommand("clipper_save")

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

; Genau direct control hotkeys
u::QueueCommand("genau_center_down")
i::QueueCommand("genau_amplitude_up")
o::QueueCommand("genau_center_up")
j::QueueCommand("genau_speed_down")
k::QueueCommand("genau_amplitude_down")
l::QueueCommand("genau_speed_up")
c::QueueCommand("genau_toggle_cruise")
m::QueueCommand("genau_prev_clip")
SC033::QueueCommand("genau_cycle_shape")
SC034::QueueCommand("genau_next_clip")
SC035::QueueCommand("genau_toggle_auto")

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
    } else if (SubStr(action, 1, 8) = "tooltip ") {
        ShowBriefTooltip(SubStr(action, 9))
    }
}

ShowBriefTooltip(msg) {
    ToolTip(msg)
    SetTimer(ClearBriefTooltip, -1500)
}

ClearBriefTooltip() {
    ToolTip()
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

ShowWindowsBridgeLog(*) {
    global WINDOWS_BRIDGE_LOG_FILE
    Run('notepad.exe "' . WINDOWS_BRIDGE_LOG_FILE . '"')
}
