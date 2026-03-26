#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent
DetectHiddenWindows False
SetTitleMatchMode 2

; Minimal AHK hotkey script — launched by the Python orchestrator.
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
VLC_PASS := RequireManifestValue("controller", "vlc_pass")
ROBOT_HAND_PY := RequireManifestValue("executables", "python_exe")
BRIDGE_COMMAND_DISPATCH_MODULE := RequireManifestValue("modules", "bridge_command_dispatch_module")
DASHBOARD_STATE_FILE := RequireManifestValue("commands", "dashboard_state_file")
DASHBOARD_CMD_FILE := RequireManifestValue("commands", "dashboard_cmd_file")
DASHBOARD_ENABLED := RequireManifestValue("dashboard", "enabled") = "1"
CONFIG_PATH := RequireManifestValue("runtime", "config_path")
PROJECT_DIR := RequireManifestValue("runtime", "project_dir")
WINDOWS_BRIDGE_LOG_FILE := RequireManifestValue("runtime", "windows_bridge_log_file")
STATE_DIR := GetParentDir(WINDOWS_BRIDGE_LOG_FILE)
ICON_PATH := PROJECT_DIR . "\icon.ico"

; Read PIDs written by Python orchestrator.
pid1 := IniRead(PIDS_FILE_PATH, "pids", "primary_pid", "0") + 0
pid2 := IniRead(PIDS_FILE_PATH, "pids", "portrait_pid", "0") + 0
pid3 := IniRead(PIDS_FILE_PATH, "pids", "landscape_pid", "0") + 0
pidM := IniRead(PIDS_FILE_PATH, "pids", "mfp_pid", "0") + 0
pidD := IniRead(PIDS_FILE_PATH, "pids", "dashboard_pid", "0") + 0
pidR := IniRead(PIDS_FILE_PATH, "pids", "robot_hand_pid", "0") + 0
pidA := IniRead(PIDS_FILE_PATH, "pids", "audio_pid", "0") + 0

; Mutable state — synced from Python's shared state file.
locked2 := false
locked3 := false
robotHandMode := false
fModeEnabled := false
omniPaused := false

SHARED_STATE_FILE := STATE_DIR . "\shared_bridge_state.ini"
AHK_CMD_FILE := STATE_DIR . "\ahk_cmd.txt"

Q(s) => Format('"{1}"', s)

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

Log("Hotkey script started with PIDs: primary=" . pid1 . " mfp=" . pidM . " portrait=" . pid2 . " landscape=" . pid3)

; -------------------- HOTKEYS --------------------

#SuspendExempt true
^!q::ExitApp()
Esc::{
    try FileDelete(DASHBOARD_CMD_FILE)
    FileAppend("omnipause_toggle", DASHBOARD_CMD_FILE, "UTF-8")
}
#SuspendExempt false

[::DispatchBridgeCommand("primary_prev")
SC01A::DispatchBridgeCommand("primary_prev")
]::DispatchBridgeCommand("primary_next")
SC01B::DispatchBridgeCommand("primary_next")
r::DispatchBridgeCommand("robot_toggle")
$f::DispatchBridgeCommand("fmode_toggle")

\::{
    if (robotHandMode) {
        DispatchBridgeCommand("quarter_button")
    } else {
        try FileDelete(DASHBOARD_CMD_FILE)
        FileAppend("open_file_dialog", DASHBOARD_CMD_FILE, "UTF-8")
    }
}

-::try ControlSend("!{Left}", , "ahk_pid " pid1)
=::try ControlSend("!{Right}", , "ahk_pid " pid1)
Left::DispatchBridgeCommand("portrait_prev")
Right::DispatchBridgeCommand("portrait_next")
Up::DispatchBridgeCommand("portrait_trash")
Down::DispatchBridgeCommand("portrait_lock")
a::DispatchBridgeCommand("landscape_prev")
d::DispatchBridgeCommand("landscape_next")
w::DispatchBridgeCommand("landscape_trash")
s::DispatchBridgeCommand("landscape_lock")

; -------------------- CORE FUNCTIONS --------------------

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
    global locked2, locked3, robotHandMode, fModeEnabled, omniPaused, SHARED_STATE_FILE
    if !FileExist(SHARED_STATE_FILE)
        return
    try {
        locked2 := IniRead(SHARED_STATE_FILE, "state", "locked2", "0") = "1"
        locked3 := IniRead(SHARED_STATE_FILE, "state", "locked3", "0") = "1"
        robotHandMode := IniRead(SHARED_STATE_FILE, "state", "robot_hand_mode", "0") = "1"
        fModeEnabled := IniRead(SHARED_STATE_FILE, "state", "f_mode_enabled", "0") = "1"
        omniPaused := IniRead(SHARED_STATE_FILE, "state", "omni_paused", "0") = "1"
    }
}

DispatchBridgeCommand(cmd) {
    Critical
    global ROBOT_HAND_PY, BRIDGE_COMMAND_DISPATCH_MODULE, PROJECT_DIR, CONFIG_PATH, VLC_PASS
    global DASHBOARD_ENABLED, DASHBOARD_STATE_FILE
    global locked2, locked3, robotHandMode, fModeEnabled, omniPaused
    global pid1, pidM

    ReadSharedState()
    mfpAlive := pidM && ProcessExist(pidM)
    resultPath := BuildBridgeDispatchResultPath()
    args := Q(cmd)
        . " --result-file " . Q(resultPath)
        . " --config-path " . Q(CONFIG_PATH)
        . " --vlc-password " . Q(VLC_PASS)
        . " --locked2 " . (locked2 ? "1" : "0")
        . " --locked3 " . (locked3 ? "1" : "0")
        . " --robot-hand-mode " . (robotHandMode ? "1" : "0")
        . " --f-mode-enabled " . (fModeEnabled ? "1" : "0")
        . " --omni-paused " . (omniPaused ? "1" : "0")
        . " --dashboard-state-file " . Q(DASHBOARD_STATE_FILE)
        . " --shared-state-file " . Q(SHARED_STATE_FILE)
        . " --dashboard-enabled " . (DASHBOARD_ENABLED ? "1" : "0")
        . " --mfp-alive " . (mfpAlive ? "1" : "0")
    pythonCmd := Q(ROBOT_HAND_PY) . " -m " . BRIDGE_COMMAND_DISPATCH_MODULE . " " . args
    if (RunHiddenWait(pythonCmd, PROJECT_DIR) != 0)
        return
    if !FileExist(resultPath)
        return

    locked2 := IniRead(resultPath, "state", "locked2", "0") = "1"
    locked3 := IniRead(resultPath, "state", "locked3", "0") = "1"
    robotHandMode := IniRead(resultPath, "state", "robot_hand_mode", "0") = "1"
    fModeEnabled := IniRead(resultPath, "state", "f_mode_enabled", "0") = "1"
    omniPaused := IniRead(resultPath, "state", "omni_paused", "0") = "1"

    logMsg := IniRead(resultPath, "state", "log_message", "")
    if (logMsg != "")
        Log(logMsg)

    opCount := IniRead(resultPath, "ops", "count", "0") + 0
    Loop opCount {
        section := "op_" . (A_Index - 1)
        op := IniRead(resultPath, section, "op", "")
        title := IniRead(resultPath, section, "title", "")
        key := IniRead(resultPath, section, "key", "")
        value := IniRead(resultPath, section, "value", "1") = "1"

        switch op {
            case "set_topmost":
                if (title != "")
                    try WinSetAlwaysOnTop(value, title)
            case "activate":
                if (title != "")
                    try WinActivate(title)
            case "show":
                if (title != "")
                    try WinShow(title)
            case "hide":
                if (title != "")
                    try WinHide(title)
            case "suspend_hotkeys":
                Suspend true
            case "unsuspend_hotkeys":
                Suspend false
            case "send_key":
                if (key != "")
                    SendToPid(pid1, key)
        }
    }

    try FileDelete(resultPath)
}

; -------------------- UTILITIES --------------------

SendToPid(pid, keys) {
    try ControlSend(keys, , "ahk_pid " pid)
}

BuildBridgeDispatchResultPath() {
    global STATE_DIR
    static counter := 0
    counter += 1
    return STATE_DIR . "\bridge_dispatch_result_" . A_TickCount . "_" . counter . ".ini"
}

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

RunHiddenWait(cmdLine, workDir := "") {
    static STARTF_USESHOWWINDOW     := 0x00000001
    static STARTF_FORCEOFFFEEDBACK  := 0x00000080
    static SW_HIDE                  := 0
    static CREATE_NO_WINDOW         := 0x08000000
    static INFINITE                 := 0xFFFFFFFF

    siSize := 104
    si := Buffer(siSize, 0)
    NumPut("UInt",   siSize,                                           si, 0)
    NumPut("UInt",   STARTF_USESHOWWINDOW | STARTF_FORCEOFFFEEDBACK,   si, 60)
    NumPut("UShort", SW_HIDE,                                          si, 64)

    pi := Buffer(24, 0)

    if !DllCall("kernel32\CreateProcessW"
        , "Ptr",  0
        , "Str",  cmdLine
        , "Ptr",  0
        , "Ptr",  0
        , "Int",  0
        , "UInt", CREATE_NO_WINDOW
        , "Ptr",  0
        , "Str",  workDir
        , "Ptr",  si
        , "Ptr",  pi
        , "Int")
        return -1

    hProcess := NumGet(pi, 0, "Ptr")
    hThread  := NumGet(pi, 8, "Ptr")

    DllCall("kernel32\WaitForSingleObject", "Ptr", hProcess, "UInt", INFINITE)

    exitCode := 0
    DllCall("kernel32\GetExitCodeProcess", "Ptr", hProcess, "UIntP", &exitCode)
    DllCall("kernel32\CloseHandle", "Ptr", hThread)
    DllCall("kernel32\CloseHandle", "Ptr", hProcess)
    return exitCode
}

Base64EncodeUtf8(s) {
    byteLen := StrPut(s, "UTF-8") - 1
    buf := Buffer(byteLen, 0)
    StrPut(s, buf, "UTF-8")
    DllCall("Crypt32\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", byteLen, "UInt", 0x1, "Ptr", 0, "UIntP", &outChars := 0)
    out := Buffer(outChars * 2, 0)
    DllCall("Crypt32\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", byteLen, "UInt", 0x1, "Ptr", out.Ptr, "UIntP", &outChars)
    return StrGet(out, outChars, "UTF-16")
}
