#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent
DetectHiddenWindows False
SetTitleMatchMode 2

; Args:
; 1 CONTROLLER_MANIFEST_PATH
if (A_Args.Length < 1) {
    MsgBox("Not enough arguments passed to controller. Got " . A_Args.Length, "fun_time", "Iconx")
    ExitApp 2
}

CONTROLLER_MANIFEST_PATH := A_Args[1]
VLC_EXE := RequireManifestValue("executables", "vlc_exe")
MFP_EXE := RequireManifestValue("executables", "mfp_exe")
PRIMARY_VLC_SOURCES := RequireManifestValue("media", "primary_vlc_sources")
PORTRAIT_DIR := RequireManifestValue("media", "portrait_dirs")
LANDSCAPE_DIR := RequireManifestValue("media", "landscape_dirs")
WEIRD_DIR := RequireManifestValue("media", "weird_dir")
FAVS_FILE := RequireManifestValue("media", "favs_file")
PRIMARY_VLC_PORT := RequireManifestValue("controller", "primary_vlc_port")
VLC2_PORT := RequireManifestValue("controller", "vlc2_port")
VLC3_PORT := RequireManifestValue("controller", "vlc3_port")
VLC_PASS := RequireManifestValue("controller", "vlc_pass")
ROBOT_HAND_PY := RequireManifestValue("executables", "python_exe")
ROBOT_HAND_MODULE := RequireManifestValue("modules", "robot_hand_module")
DASHBOARD_MODULE := RequireManifestValue("modules", "dashboard_module")
CONTROLLER_MODES_MODULE := RequireManifestValue("modules", "controller_modes_module")
CONTROLLER_LOCK_MODULE := RequireManifestValue("modules", "controller_lock_module")
CONTROLLER_ROBOT_HAND_MODULE := RequireManifestValue("modules", "controller_robot_hand_module")
CONTROLLER_OMNIPAUSE_MODULE := RequireManifestValue("modules", "controller_omnipause_module")
CONTROLLER_WINDOW_LAYOUT_MODULE := RequireManifestValue("modules", "controller_window_layout_module")
CONTROLLER_VLC_ACTIONS_MODULE := RequireManifestValue("modules", "controller_vlc_actions_module")
CONTROLLER_RANDOM_FAVS_BROWSER_MODULE := RequireManifestValue("modules", "controller_random_favs_browser_module")
CONTROLLER_STARTUP_MODULE := RequireManifestValue("modules", "controller_startup_module")
CONTROLLER_DASHBOARD_BRIDGE_MODULE := RequireManifestValue("modules", "controller_dashboard_bridge_module")
ROBOT_HAND_CLIPS := RequireManifestValue("media", "robot_hand_clips")
ROBOT_HAND_AUDIO_MODULE := RequireManifestValue("modules", "audio_module")
ROBOT_HAND_AUDIO := RequireManifestValue("media", "robot_hand_audio")
ROBOT_HAND_MODE_FILE := RequireManifestValue("commands", "robot_hand_mode_file")
ROBOT_HAND_CMD_FILE := RequireManifestValue("commands", "robot_hand_cmd_file")
ROBOT_HAND_ENABLED_FILE := RequireManifestValue("commands", "robot_hand_enabled_file")
ROBOT_HAND_PAUSED_FILE := RequireManifestValue("commands", "robot_hand_paused_file")
BROKER_CMD_FILE := RequireManifestValue("commands", "broker_cmd_file")
AUDIO_CMD_FILE := RequireManifestValue("commands", "audio_cmd_file")
AUDIO_PAUSED_FILE := RequireManifestValue("commands", "audio_paused_file")
DASHBOARD_STATE_FILE := RequireManifestValue("commands", "dashboard_state_file")
DASHBOARD_CMD_FILE := RequireManifestValue("commands", "dashboard_cmd_file")
DASHBOARD_ENABLED := RequireManifestValue("dashboard", "enabled") = "1"
MAIN_MONITOR := RequireManifestValue("layout", "main_monitor")
SECONDARY_MONITOR := RequireManifestValue("layout", "secondary_monitor")
PRIMARY_TOP_RATIO := RequireManifestValue("layout", "primary_top_ratio")
LANDSCAPE_WIDTH_RATIO := RequireManifestValue("layout", "landscape_width_ratio")
MFP_WIDTH_RATIO := RequireManifestValue("layout", "mfp_width_ratio")
MFP_HEIGHT_RATIO := RequireManifestValue("layout", "mfp_height_ratio")
CONTROLLER_LOG_FILE := RequireManifestValue("runtime", "controller_log_file")
RANDOM_FAVS_BROWSER_SHORTCUT_PATH := RequireManifestValue("random_favs_browser", "shortcut_path")
RANDOM_FAVS_BROWSER_MANIFEST_FILE := RequireManifestValue("random_favs_browser", "manifest_file")
RANDOM_FAVS_BROWSER_ENABLED := RequireManifestValue("random_favs_browser", "enabled") = "1"
CONFIG_PATH := RequireManifestValue("runtime", "config_path")
PROJECT_DIR := RequireManifestValue("runtime", "project_dir")
ICON_PATH := PROJECT_DIR . "\icon.ico"
STATE_DIR := GetParentDir(CONTROLLER_LOG_FILE)

; IMPORTANT: VLC web interface commonly uses BLANK username + password.
VLC_USER := ""

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
LABEL_PRIMARY_VLC := "Non-AI VLC"
LABEL_PRIMARY_ROBOT := "Non-AI Robot Hand"
LABEL_PORTRAIT_VLC := "Portrait AI VLC"
LABEL_LANDSCAPE_VLC := "Landscape AI VLC"
LABEL_OSR2 := "OSR2"
LABEL_MFP := "MFP"
LABEL_BROKER := "Broker"
LABEL_CONTROLLER := "Controller"
LABEL_F_MODE := "F-Mode"

COLOR_BG := "20262C"
COLOR_PANEL := "2A3038"
COLOR_TEXT := "F4F7FA"
COLOR_MUTED := "AEB7C2"
COLOR_ACTIVE := "1F6F52"
COLOR_ACTIVE_ALT := "2C8A65"
COLOR_DISABLED := "6C1F1F"
COLOR_WARNING := "8A6A2C"
COLOR_LINK_ON := "3A7AFE"
COLOR_LINK_OFF := "7C8694"

Q(s) => Format('"{1}"', s)

#Include controller_windows.ahk
#Include controller_runtime.ahk
#Include controller_actions.ahk

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
    global CONTROLLER_LOG_FILE
    try {
        FileAppend(FormatTime(, "yyyy-MM-dd HH:mm:ss") . " " . msg . "`r`n", CONTROLLER_LOG_FILE, "UTF-8")
    }
}

WriteRawStateFile(path, text) {
    SplitPath(path, , &dirPath)
    if (dirPath != "")
        DirCreate(dirPath)

    tries := 0
    while (tries < 8) {
        file := ""
        try {
            file := FileOpen(path, "w", "UTF-8-RAW")
            if (!file)
                throw Error("Failed to open state file: " . path)
            file.Write(text)
            file.Close()
            return
        } catch {
            try {
                if (IsObject(file))
                    file.Close()
            }
            Sleep 30
            tries += 1
        }
    }
    throw Error("Failed to write state file after retries: " . path)
}

RequireManifestValue(section, key) {
    global CONTROLLER_MANIFEST_PATH
    missing := "__missing__"
    value := IniRead(CONTROLLER_MANIFEST_PATH, section, key, missing)
    if (value = missing) {
        MsgBox("Missing controller manifest value [" . section . "] " . key, "fun_time", "Iconx")
        ExitApp 2
    }
    return value
}

RunApp(exe, args) {
    global PROJECT_DIR
    cmd := Q(exe)
    if (args != "")
        cmd .= " " . args
    Run(cmd, PROJECT_DIR, , &pid)
    return pid
}

RunVLC(args, mediaPath) {
    mediaArgs := ""
    for pathPart in StrSplit(mediaPath, "|") {
        pathTrimmed := Trim(pathPart)
        if (pathTrimmed != "")
            mediaArgs .= " " . Q(pathTrimmed)
    }
    cmd := Q(VLC_EXE) . " " . args . mediaArgs
    Run(cmd, , , &pid)
    return pid
}

RunDetached(cmdLine) {
    Run(cmdLine, , , &pid)
    return pid
}

RunControllerModesAction(args) {
    global ROBOT_HAND_PY, CONTROLLER_MODES_MODULE, PROJECT_DIR
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_MODES_MODULE . " " . args
    return RunWait(cmd, PROJECT_DIR, "Hide")
}

RunControllerLockAction(action, which, locked, currentPath, planPath, extraArgs := "") {
    global ROBOT_HAND_PY, CONTROLLER_LOCK_MODULE, PROJECT_DIR
    args := action
        . " --which " . which
        . " --locked " . (locked ? "1" : "0")
        . " --current-path " . Q(currentPath)
        . " --plan-file " . Q(planPath)
    if (extraArgs != "")
        args .= " " . extraArgs
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_LOCK_MODULE . " " . args
    if (RunWait(cmd, PROJECT_DIR, "Hide") != 0)
        return ""
    return LoadLockActionPlan(planPath)
}

RunControllerRobotHandAction(action, robotHandModeOn, enabled, omniPausedOn, planPath, extraArgs := "") {
    global ROBOT_HAND_PY, CONTROLLER_ROBOT_HAND_MODULE, PROJECT_DIR
    args := action
        . " --robot-hand-mode-on " . (robotHandModeOn ? "1" : "0")
        . " --enabled " . (enabled ? "1" : "0")
        . " --mode-state-on " . (RobotHandModeState() = "1" ? "1" : "0")
        . " --omni-paused " . (omniPausedOn ? "1" : "0")
        . " --plan-file " . Q(planPath)
    if (extraArgs != "")
        args .= " " . extraArgs
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_ROBOT_HAND_MODULE . " " . args
    if (RunWait(cmd, PROJECT_DIR, "Hide") != 0)
        return ""
    return LoadRobotHandActionPlan(planPath)
}

RunControllerOmniPauseAction(action, omniPausedOn, robotHandModeOn, skipPrimaryResume, planPath, extraArgs := "") {
    global ROBOT_HAND_PY, CONTROLLER_OMNIPAUSE_MODULE, PROJECT_DIR
    args := action
        . " --omni-paused " . (omniPausedOn ? "1" : "0")
        . " --robot-hand-mode-on " . (robotHandModeOn ? "1" : "0")
        . " --skip-primary-resume " . (skipPrimaryResume ? "1" : "0")
        . " --plan-file " . Q(planPath)
    if (extraArgs != "")
        args .= " " . extraArgs
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_OMNIPAUSE_MODULE . " " . args
    if (RunWait(cmd, PROJECT_DIR, "Hide") != 0)
        return ""
    return LoadOmniPauseActionPlan(planPath)
}

LoadLockActionPlan(path) {
    if !FileExist(path)
        return ""
    plan := Map()
    plan["next_locked"] := IniRead(path, "plan", "next_locked", "0") = "1"
    plan["repeat_mode"] := IniRead(path, "plan", "repeat_mode", "")
    plan["ensure_in_favs"] := IniRead(path, "plan", "ensure_in_favs", "0") = "1"
    plan["remove_from_favs"] := IniRead(path, "plan", "remove_from_favs", "0") = "1"
    plan["advance_playlist"] := IniRead(path, "plan", "advance_playlist", "0") = "1"
    plan["move_to_weird"] := IniRead(path, "plan", "move_to_weird", "0") = "1"
    plan["log_message"] := IniRead(path, "plan", "log_message", "")
    try FileDelete(path)
    return plan
}

LoadRobotHandActionPlan(path) {
    if !FileExist(path)
        return ""
    plan := Map()
    plan["write_enabled"] := IniRead(path, "plan", "write_enabled", "0") = "1"
    plan["enabled_value"] := IniRead(path, "plan", "enabled_value", "0") = "1"
    plan["next_robot_hand_mode"] := IniRead(path, "plan", "next_robot_hand_mode", "0") = "1"
    plan["enforce_outputs"] := IniRead(path, "plan", "enforce_outputs", "0") = "1"
    plan["enforce_active"] := IniRead(path, "plan", "enforce_active", "0") = "1"
    plan["is_transition"] := IniRead(path, "plan", "is_transition", "0") = "1"
    plan["log_message"] := IniRead(path, "plan", "log_message", "")
    try FileDelete(path)
    return plan
}

LoadOmniPauseActionPlan(path) {
    if !FileExist(path)
        return ""
    plan := Map()
    plan["action"] := IniRead(path, "plan", "action", "")
    plan["next_omni_paused"] := IniRead(path, "plan", "next_omni_paused", "0") = "1"
    plan["robot_hand_branch"] := IniRead(path, "plan", "robot_hand_branch", "0") = "1"
    plan["resume_primary_playback"] := IniRead(path, "plan", "resume_primary_playback", "0") = "1"
    plan["log_message"] := IniRead(path, "plan", "log_message", "")
    try FileDelete(path)
    return plan
}

LoadModesActionResult(path) {
    if !FileExist(path)
        return ""
    result := Map()
    result["next_locked2"] := IniRead(path, "result", "next_locked2", "0") = "1"
    result["next_locked3"] := IniRead(path, "result", "next_locked3", "0") = "1"
    try FileDelete(path)
    return result
}

LoadStartupActionResult(path) {
    if !FileExist(path)
        return ""
    result := Map()
    result["robot_hand_pid"] := IniRead(path, "result", "robot_hand_pid", "0") + 0
    result["audio_pid"] := IniRead(path, "result", "audio_pid", "0") + 0
    try FileDelete(path)
    return result
}

BuildLockPlanPath(which) {
    global STATE_DIR
    return STATE_DIR . "\lock_action_plan_" . which . ".ini"
}

BuildRobotHandPlanPath() {
    global STATE_DIR
    return STATE_DIR . "\robot_hand_action_plan.ini"
}

BuildOmniPausePlanPath() {
    global STATE_DIR
    return STATE_DIR . "\omnipause_action_plan.ini"
}

BuildModesResultPath() {
    global STATE_DIR
    return STATE_DIR . "\modes_action_result.ini"
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

BuildVlcQueryOutputPath(prefix) {
    global STATE_DIR
    static counter := 0
    counter += 1
    return STATE_DIR . "\" . prefix . "_" . A_TickCount . "_" . counter . ".txt"
}

LaunchDashboardApp(x, y, w, h, mfpPid := 0) {
    global ROBOT_HAND_PY, DASHBOARD_MODULE, CONTROLLER_MANIFEST_PATH
    args := "-m " . DASHBOARD_MODULE
        . " " . Q(CONTROLLER_MANIFEST_PATH)
        . " --x " . x
        . " --y " . y
        . " --width " . w
        . " --height " . h
    if (mfpPid)
        args .= " --mfp-pid " . mfpPid
    return RunApp(ROBOT_HAND_PY, args)
}

RunControllerWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath) {
    global ROBOT_HAND_PY, CONTROLLER_WINDOW_LAYOUT_MODULE, PROJECT_DIR
    global PRIMARY_TOP_RATIO, LANDSCAPE_WIDTH_RATIO, MFP_WIDTH_RATIO, MFP_HEIGHT_RATIO

    args := "write-plan"
        . " --main-x " . mainRect["x"]
        . " --main-y " . mainRect["y"]
        . " --main-width " . mainRect["w"]
        . " --main-height " . mainRect["h"]
        . " --secondary-x " . secondaryRect["x"]
        . " --secondary-y " . secondaryRect["y"]
        . " --secondary-width " . secondaryRect["w"]
        . " --secondary-height " . secondaryRect["h"]
        . " --primary-top-ratio " . PRIMARY_TOP_RATIO
        . " --landscape-width-ratio " . LANDSCAPE_WIDTH_RATIO
        . " --mfp-width-ratio " . MFP_WIDTH_RATIO
        . " --mfp-height-ratio " . MFP_HEIGHT_RATIO
        . " --mfp-width " . mfpW
        . " --mfp-height " . mfpH
        . " --plan-file " . Q(planPath)
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_WINDOW_LAYOUT_MODULE . " " . args
    if (RunWait(cmd, PROJECT_DIR, "Hide") != 0)
        return ""
    return LoadWindowLayoutPlan(planPath)
}

RunControllerVlcAction(args) {
    global ROBOT_HAND_PY, CONTROLLER_VLC_ACTIONS_MODULE, PROJECT_DIR
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_VLC_ACTIONS_MODULE . " " . args
    return RunWait(cmd, PROJECT_DIR, "Hide")
}

RunControllerStartupAction(args) {
    global ROBOT_HAND_PY, CONTROLLER_STARTUP_MODULE, PROJECT_DIR
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_STARTUP_MODULE . " " . args
    return RunWait(cmd, PROJECT_DIR, "Hide")
}

RunControllerDashboardBridgeAction(args) {
    global ROBOT_HAND_PY, CONTROLLER_DASHBOARD_BRIDGE_MODULE, PROJECT_DIR
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_DASHBOARD_BRIDGE_MODULE . " " . args
    return RunWait(cmd, PROJECT_DIR, "Hide")
}

LaunchRandomFavsBrowserViaPython(manifestPath, shortcutTarget, shortcutWorkDir, shortcutArgs) {
    global ROBOT_HAND_PY, CONTROLLER_RANDOM_FAVS_BROWSER_MODULE, PROJECT_DIR
    encodedShortcutArgs := Base64EncodeUtf8(shortcutArgs)
    args := "launch"
        . " --manifest-file " . Q(manifestPath)
        . " --shortcut-target " . Q(shortcutTarget)
        . " --shortcut-work-dir " . Q(shortcutWorkDir)
        . " --shortcut-args-b64 " . Q(encodedShortcutArgs)
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_RANDOM_FAVS_BROWSER_MODULE . " " . args
    exitCode := RunWait(cmd, PROJECT_DIR, "Hide")
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
    try RunWait(A_ComSpec . " /c taskkill /PID " . pid . " /T /F", , "Hide")
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
    plan := RunControllerWindowLayout(mainRect, secondaryRect, mfpW, mfpH, planPath)
    if (!IsObject(plan))
        throw Error("Failed to build window layout plan")
}

; lifecycle/runtime orchestration moved to controller_runtime.ahk

StartController()

; -------------------- HOTKEYS --------------------

#SuspendExempt true
^!q::ShutdownAll()
Esc::OmniPauseToggle()
#SuspendExempt false

[::HandlePrevAction()
SC01A::HandlePrevAction()

]::HandleNextAction()
SC01B::HandleNextAction()

r::ToggleRobotHandEnabled()
$f::ToggleFMode()

\::{
    if (EffectiveRobotHandModeState() = "1") {
        QueueRobotHandOffsetQuarterCycle()
    } else {
        ; Managed file-open flow: pause globally while browsing, then resume without
        ; toggling primary VLC playback so newly selected media keeps playing.
        OpenPrimaryVlcFileDialogWithManagedOmniPause()
    }
}

-::try ControlSend("!{Left}", , "ahk_pid " pid1)
=::try ControlSend("!{Right}", , "ahk_pid " pid1)
Left::{
    CancelLock(2)
    SendVlcCommand(VLC2_PORT, "pl_previous")
}
Right::{
    CancelLock(2)
    SendVlcCommand(VLC2_PORT, "pl_next")
}
Up::Discard(2)
Down::ToggleLock(2)

a::{
    CancelLock(3)
    SendVlcCommand(VLC3_PORT, "pl_previous")
}
d::{
    CancelLock(3)
    SendVlcCommand(VLC3_PORT, "pl_next")
}
w::Discard(3)
s::ToggleLock(3)

; =====================================================================
; ========================= IMPLEMENTATION ============================
; =====================================================================

; -------------------- HTTP --------------------

; action/runtime glue moved to controller_actions.ahk

