#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent
DetectHiddenWindows False
SetTitleMatchMode 2

; Args:
; 1 WINDOWS_BRIDGE_MANIFEST_PATH
if (A_Args.Length < 1) {
    MsgBox("Not enough arguments passed to Windows bridge. Got " . A_Args.Length, "fun_time", "Iconx")
    ExitApp 2
}

WINDOWS_BRIDGE_MANIFEST_PATH := A_Args[1]
VLC_PASS := RequireManifestValue("controller", "vlc_pass")
ROBOT_HAND_PY := RequireManifestValue("executables", "python_exe")
WINDOWS_BRIDGE_WINDOW_LAYOUT_MODULE := RequireManifestValue("modules", "windows_bridge_window_layout_module")
WINDOWS_BRIDGE_RANDOM_FAVS_BROWSER_MODULE := RequireManifestValue("modules", "windows_bridge_random_favs_browser_module")
WINDOWS_BRIDGE_STARTUP_MODULE := RequireManifestValue("modules", "windows_bridge_startup_module")
BRIDGE_COMMAND_DISPATCH_MODULE := RequireManifestValue("modules", "bridge_command_dispatch_module")
DASHBOARD_STATE_FILE := RequireManifestValue("commands", "dashboard_state_file")
DASHBOARD_CMD_FILE := RequireManifestValue("commands", "dashboard_cmd_file")
DASHBOARD_ENABLED := RequireManifestValue("dashboard", "enabled") = "1"
MAIN_MONITOR := RequireManifestValue("layout", "main_monitor")
SECONDARY_MONITOR := RequireManifestValue("layout", "secondary_monitor")
LANDSCAPE_WIDTH_RATIO := RequireManifestValue("layout", "landscape_width_ratio")
MFP_WIDTH_RATIO := RequireManifestValue("layout", "mfp_width_ratio")
MFP_HEIGHT_RATIO := RequireManifestValue("layout", "mfp_height_ratio")
WINDOWS_BRIDGE_LOG_FILE := RequireManifestValue("runtime", "windows_bridge_log_file")
RANDOM_FAVS_BROWSER_SHORTCUT_PATH := RequireManifestValue("random_favs_browser", "shortcut_path")
RANDOM_FAVS_BROWSER_MANIFEST_FILE := RequireManifestValue("random_favs_browser", "manifest_file")
RANDOM_FAVS_BROWSER_ENABLED := RequireManifestValue("random_favs_browser", "enabled") = "1"
CONFIG_PATH := RequireManifestValue("runtime", "config_path")
PROJECT_DIR := RequireManifestValue("runtime", "project_dir")
ICON_PATH := PROJECT_DIR . "\icon.ico"
STATE_DIR := GetParentDir(WINDOWS_BRIDGE_LOG_FILE)

locked2 := false
locked3 := false

robotHandMode := false
fModeEnabled := false
omniPaused := false
isShuttingDown := false
pid1 := 0
pid2 := 0
pid3 := 0
pidM := 0
pidD := 0
pidR := 0
pidA := 0

Q(s) => Format('"{1}"', s)

#Include windows_bridge_windows.ahk
#Include windows_bridge_runtime.ahk
#Include windows_bridge_actions.ahk

GetParentDir(path) {
    SplitPath(path, , &dirPath)
    return dirPath
}

Join(a, b, c := "", d := "", e := "") {
    out := a
    for v in [b,c,d,e] {
        if (v != "")
            out .= " " . v
    }
    return out
}

Clamp01(value) {
    numeric := value + 0
    if (numeric < 0)
        return 0.0
    if (numeric > 1)
        return 1.0
    return numeric
}

Log(msg) {
    global WINDOWS_BRIDGE_LOG_FILE
    try {
        FileAppend(FormatTime(, "yyyy-MM-dd HH:mm:ss") . " " . msg . "`r`n", WINDOWS_BRIDGE_LOG_FILE, "UTF-8")
    }
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

RunHiddenWait(cmdLine, workDir := "") {
    ; Launch a hidden subprocess WITHOUT triggering the Windows "app starting"
    ; cursor.  AHK's built-in RunWait always causes the hourglass/spinner
    ; because it does not set STARTF_FORCEOFFFEEDBACK.  Since the sync timer
    ; launches Python every 200 ms, the cursor flickers non-stop without this.
    static STARTF_USESHOWWINDOW     := 0x00000001
    static STARTF_FORCEOFFFEEDBACK  := 0x00000080
    static SW_HIDE                  := 0
    static CREATE_NO_WINDOW         := 0x08000000
    static INFINITE                 := 0xFFFFFFFF

    siSize := 104       ; sizeof(STARTUPINFOW) on x64
    si := Buffer(siSize, 0)
    NumPut("UInt",   siSize,                                           si, 0)   ; cb
    NumPut("UInt",   STARTF_USESHOWWINDOW | STARTF_FORCEOFFFEEDBACK,   si, 60)  ; dwFlags
    NumPut("UShort", SW_HIDE,                                          si, 64)  ; wShowWindow

    pi := Buffer(24, 0) ; sizeof(PROCESS_INFORMATION) on x64

    if !DllCall("kernel32\CreateProcessW"
        , "Ptr",  0              ; lpApplicationName
        , "Str",  cmdLine        ; lpCommandLine (mutable copy)
        , "Ptr",  0              ; lpProcessAttributes
        , "Ptr",  0              ; lpThreadAttributes
        , "Int",  0              ; bInheritHandles
        , "UInt", CREATE_NO_WINDOW
        , "Ptr",  0              ; lpEnvironment
        , "Str",  workDir        ; lpCurrentDirectory
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

LoadStartupActionResult(path) {
    if !FileExist(path)
        return ""
    result := Map()
    result["dashboard_pid"] := IniRead(path, "result", "dashboard_pid", "0") + 0
    result["primary_pid"] := IniRead(path, "result", "primary_pid", "0") + 0
    result["mfp_pid"] := IniRead(path, "result", "mfp_pid", "0") + 0
    result["portrait_pid"] := IniRead(path, "result", "portrait_pid", "0") + 0
    result["landscape_pid"] := IniRead(path, "result", "landscape_pid", "0") + 0
    result["robot_hand_pid"] := IniRead(path, "result", "robot_hand_pid", "0") + 0
    result["audio_pid"] := IniRead(path, "result", "audio_pid", "0") + 0
    try FileDelete(path)
    return result
}

BuildStartupResultPath() {
    global STATE_DIR
    static counter := 0
    counter += 1
    return STATE_DIR . "\startup_action_result_" . A_TickCount . "_" . counter . ".ini"
}

BuildWindowLayoutPlanPath() {
    global STATE_DIR
    static counter := 0
    counter += 1
    return STATE_DIR . "\window_layout_plan_" . A_TickCount . "_" . counter . ".ini"
}

BuildBridgeDispatchResultPath() {
    global STATE_DIR
    static counter := 0
    counter += 1
    return STATE_DIR . "\bridge_dispatch_result_" . A_TickCount . "_" . counter . ".ini"
}

RunWindowsBridgeWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath) {
    global ROBOT_HAND_PY, WINDOWS_BRIDGE_WINDOW_LAYOUT_MODULE, PROJECT_DIR
    global WINDOWS_BRIDGE_MANIFEST_PATH

    args := "write-plan"
        . " --manifest " . Q(WINDOWS_BRIDGE_MANIFEST_PATH)
        . " --main-x " . mainRect["x"]
        . " --main-y " . mainRect["y"]
        . " --main-width " . mainRect["w"]
        . " --main-height " . mainRect["h"]
        . " --secondary-x " . secondaryRect["x"]
        . " --secondary-y " . secondaryRect["y"]
        . " --secondary-width " . secondaryRect["w"]
        . " --secondary-height " . secondaryRect["h"]
        . " --mfp-width " . mfpW
        . " --mfp-height " . mfpH
        . " --plan-file " . Q(planPath)
    cmd := Q(ROBOT_HAND_PY) . " -m " . WINDOWS_BRIDGE_WINDOW_LAYOUT_MODULE . " " . args
    if (RunHiddenWait(cmd, PROJECT_DIR) != 0)
        return ""
    return LoadWindowLayoutPlan(planPath)
}

RunWindowsBridgeStartupAction(args) {
    global ROBOT_HAND_PY, WINDOWS_BRIDGE_STARTUP_MODULE, PROJECT_DIR
    cmd := Q(ROBOT_HAND_PY) . " -m " . WINDOWS_BRIDGE_STARTUP_MODULE . " " . args
    return RunHiddenWait(cmd, PROJECT_DIR)
}

LaunchRandomFavsBrowserViaPython(manifestPath, shortcutTarget, shortcutWorkDir, shortcutArgs) {
    global ROBOT_HAND_PY, WINDOWS_BRIDGE_RANDOM_FAVS_BROWSER_MODULE, PROJECT_DIR
    encodedShortcutArgs := Base64EncodeUtf8(shortcutArgs)
    args := "launch"
        . " --manifest-file " . Q(manifestPath)
        . " --shortcut-target " . Q(shortcutTarget)
        . " --shortcut-work-dir " . Q(shortcutWorkDir)
        . " --shortcut-args-b64 " . Q(encodedShortcutArgs)
    cmd := Q(ROBOT_HAND_PY) . " -m " . WINDOWS_BRIDGE_RANDOM_FAVS_BROWSER_MODULE . " " . args
    exitCode := RunHiddenWait(cmd, PROJECT_DIR)
    if (exitCode = 0)
        return true
    if (exitCode != 3)
        Log("Random Favs Browser launcher failed exitCode=" . exitCode)
    return false
}

LoadWindowLayoutPlan(path) {
    if !FileExist(path)
        return ""
    plan := Map()
    for section in ["portrait", "primary", "landscape", "mfp", "dashboard", "random_favs_browser", "robot_hand"] {
        plan[section] := Map(
            "x", IniRead(path, section, "x", "0") + 0,
            "y", IniRead(path, section, "y", "0") + 0,
            "w", IniRead(path, section, "width", "0") + 0,
            "h", IniRead(path, section, "height", "0") + 0
        )
    }
    try FileDelete(path)
    return plan
}

TryClosePid(pid) {
    if (!pid)
        return
    try WinClose("ahk_pid " pid)
}

TryKillPid(pid) {
    if (!pid)
        return
    try ProcessClose(pid)
}

ForceKillPid(pid) {
    if (!pid)
        return
    try RunHiddenWait(A_ComSpec . " /c taskkill /PID " . pid . " /T /F")
}

GetMonitorRect(index) {
    MonitorGetWorkArea(index, &left, &top, &right, &bottom)
    return Map("index", index, "x", left, "y", top, "w", right - left, "h", bottom - top)
}

GetLogicalMonitorRects(&mainRect, &secondaryRect) {
    global MAIN_MONITOR, SECONDARY_MONITOR

    configuredMain := GetMonitorRect(MAIN_MONITOR)
    configuredSecondary := GetMonitorRect(SECONDARY_MONITOR)

    ; Keep config values intuitive while correcting Windows display-number weirdness:
    ; treat the wide/landscape screen as the logical main monitor and the tall screen
    ; as the logical secondary monitor.
    if (configuredMain["w"] >= configuredMain["h"] && configuredSecondary["w"] < configuredSecondary["h"]) {
        mainRect := configuredMain
        secondaryRect := configuredSecondary
        return
    }

    if (configuredSecondary["w"] >= configuredSecondary["h"] && configuredMain["w"] < configuredMain["h"]) {
        mainRect := configuredSecondary
        secondaryRect := configuredMain
        return
    }

    ; Fallback when both screens are the same orientation: leftmost is main.
    if (configuredMain["x"] <= configuredSecondary["x"]) {
        mainRect := configuredMain
        secondaryRect := configuredSecondary
    } else {
        mainRect := configuredSecondary
        secondaryRect := configuredMain
    }
}

GetCurrentWindowLayout(&plan, mfpW := "", mfpH := "") {
    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)
    if (mfpW = "" || mfpH = "")
        GetActualMfpSize(&mfpW, &mfpH)
    planPath := BuildWindowLayoutPlanPath()
    plan := RunWindowsBridgeWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath)
    if (!IsObject(plan))
        throw Error("Failed to build window layout plan")
}

; lifecycle/runtime orchestration moved to windows_bridge_runtime.ahk

StartWindowsBridge()

; -------------------- HOTKEYS --------------------

#SuspendExempt true
^!q::ShutdownAll()
Esc::{
    HandleOmniPauseToggle()

}
#SuspendExempt false

[::{
    DispatchBridgeCommand("primary_prev")
}
SC01A::{
    DispatchBridgeCommand("primary_prev")
}

]::{
    DispatchBridgeCommand("primary_next")
}
SC01B::{
    DispatchBridgeCommand("primary_next")
}

r::{
    DispatchBridgeCommand("robot_toggle")

}
$f::{
    DispatchBridgeCommand("fmode_toggle")

}

\::{
    if (robotHandMode) {
        DispatchBridgeCommand("quarter_button")
    } else {
        ; Managed file-open flow: pause globally while browsing, then resume without
        ; toggling primary VLC playback so newly selected media keeps playing.
        OpenPrimaryVlcFileDialogWithManagedOmniPause()
    }
}

-::try ControlSend("!{Left}", , "ahk_pid " pid1)
=::try ControlSend("!{Right}", , "ahk_pid " pid1)
Left::{
    DispatchBridgeCommand("portrait_prev")

}
Right::{
    DispatchBridgeCommand("portrait_next")

}
Up::{
    DispatchBridgeCommand("portrait_trash")

}
Down::{
    DispatchBridgeCommand("portrait_lock")

}

a::{
    DispatchBridgeCommand("landscape_prev")

}
d::{
    DispatchBridgeCommand("landscape_next")

}
w::{
    DispatchBridgeCommand("landscape_trash")

}
s::{
    DispatchBridgeCommand("landscape_lock")

}

; =====================================================================
; ========================= IMPLEMENTATION ============================
; =====================================================================

; -------------------- HTTP --------------------

; action/runtime glue moved to windows_bridge_actions.ahk


