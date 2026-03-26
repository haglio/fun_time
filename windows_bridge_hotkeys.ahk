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
;   2  PIDS_FILE_PATH  (INI with [pids] section written by Python)

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

; Read PIDs written by Python orchestrator (only pid1 needed for ControlSend).
pid1 := IniRead(PIDS_FILE_PATH, "pids", "primary_pid", "0") + 0

; Mutable state — synced from Python's shared state file.
robotHandMode := false

SHARED_STATE_FILE := STATE_DIR . "\shared_bridge_state.ini"
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

Log("Hotkey script started with primary PID=" . pid1)

; -------------------- HOTKEYS --------------------

#SuspendExempt true
^!q::ExitApp()
Esc::QueueCommand("omnipause_toggle")
#SuspendExempt false

[::QueueCommand("primary_prev")
SC01A::QueueCommand("primary_prev")
]::QueueCommand("primary_next")
SC01B::QueueCommand("primary_next")
r::QueueCommand("robot_toggle")
$f::QueueCommand("fmode_toggle")

\::{
    if (robotHandMode) {
        QueueCommand("quarter_button")
    } else {
        QueueCommand("open_file_dialog")
    }
}

-::try ControlSend("!{Left}", , "ahk_pid " pid1)
=::try ControlSend("!{Right}", , "ahk_pid " pid1)
Left::QueueCommand("portrait_prev")
Right::QueueCommand("portrait_next")
Up::QueueCommand("portrait_trash")
Down::QueueCommand("portrait_lock")
a::QueueCommand("landscape_prev")
d::QueueCommand("landscape_next")
w::QueueCommand("landscape_trash")
s::QueueCommand("landscape_lock")

; -------------------- CORE FUNCTIONS --------------------

QueueCommand(cmd) {
    global DASHBOARD_CMD_FILE
    FileAppend(cmd . "`n", DASHBOARD_CMD_FILE, "UTF-8")
}

ProcessAhkCommand() {
    global AHK_CMD_FILE
    ReadSharedState()
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
    }
}

ReadSharedState() {
    global robotHandMode, SHARED_STATE_FILE
    if !FileExist(SHARED_STATE_FILE)
        return
    try {
        robotHandMode := IniRead(SHARED_STATE_FILE, "state", "robot_hand_mode", "0") = "1"
    }
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
    try {
        FileAppend(FormatTime(, "yyyy-MM-dd HH:mm:ss") . " " . msg . "`r`n", WINDOWS_BRIDGE_LOG_FILE, "UTF-8")
    }
}

ShowWindowsBridgeLog(*) {
    global WINDOWS_BRIDGE_LOG_FILE
    Run('notepad.exe "' . WINDOWS_BRIDGE_LOG_FILE . '"')
}
