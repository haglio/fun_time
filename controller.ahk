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
MEDIA_ACTIONS_MODULE := RequireManifestValue("modules", "media_actions_module")
CONTROLLER_MODES_MODULE := RequireManifestValue("modules", "controller_modes_module")
CONTROLLER_LOCK_MODULE := RequireManifestValue("modules", "controller_lock_module")
CONTROLLER_ROBOT_HAND_MODULE := RequireManifestValue("modules", "controller_robot_hand_module")
CONTROLLER_OMNIPAUSE_MODULE := RequireManifestValue("modules", "controller_omnipause_module")
CONTROLLER_WINDOW_LAYOUT_MODULE := RequireManifestValue("modules", "controller_window_layout_module")
CONTROLLER_VLC_ACTIONS_MODULE := RequireManifestValue("modules", "controller_vlc_actions_module")
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
MAIN_MONITOR := RequireManifestValue("layout", "main_monitor")
SECONDARY_MONITOR := RequireManifestValue("layout", "secondary_monitor")
PRIMARY_TOP_RATIO := RequireManifestValue("layout", "primary_top_ratio")
LANDSCAPE_WIDTH_RATIO := RequireManifestValue("layout", "landscape_width_ratio")
MFP_WIDTH_RATIO := RequireManifestValue("layout", "mfp_width_ratio")
MFP_HEIGHT_RATIO := RequireManifestValue("layout", "mfp_height_ratio")
CONTROLLER_LOG_FILE := RequireManifestValue("runtime", "controller_log_file")
RANDOM_FAVS_BROWSER_SHORTCUT_PATH := RequireManifestValue("random_favs_browser", "shortcut_path")
RANDOM_FAVS_BROWSER_MANIFEST_FILE := RequireManifestValue("random_favs_browser", "manifest_file")
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
dashboardStatusRefreshTick := 0
dashboardBrokerRunning := false
dashboardMfpConnected := false
lastDashboardSnapshotText := ""
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

RunMediaAction(action, targetPath) {
    global ROBOT_HAND_PY, MEDIA_ACTIONS_MODULE, FAVS_FILE, WEIRD_DIR, PROJECT_DIR
    if (targetPath = "")
        return
    cmd := Q(ROBOT_HAND_PY)
        . " -m " . MEDIA_ACTIONS_MODULE
        . " " . action
        . " --favs-file " . Q(FAVS_FILE)
        . " --weird-dir " . Q(WEIRD_DIR)
        . " --path " . Q(targetPath)
    RunWait(cmd, PROJECT_DIR, "Hide")
}

RunControllerModesAction(args) {
    global ROBOT_HAND_PY, CONTROLLER_MODES_MODULE, PROJECT_DIR
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_MODES_MODULE . " " . args
    return RunWait(cmd, PROJECT_DIR, "Hide")
}

RunControllerLockAction(action, which, locked, currentPath, planPath) {
    global ROBOT_HAND_PY, CONTROLLER_LOCK_MODULE, PROJECT_DIR
    args := action
        . " --which " . which
        . " --locked " . (locked ? "1" : "0")
        . " --current-path " . Q(currentPath)
        . " --plan-file " . Q(planPath)
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_LOCK_MODULE . " " . args
    if (RunWait(cmd, PROJECT_DIR, "Hide") != 0)
        return ""
    return LoadLockActionPlan(planPath)
}

RunControllerRobotHandAction(action, robotHandModeOn, enabled, omniPausedOn, planPath) {
    global ROBOT_HAND_PY, CONTROLLER_ROBOT_HAND_MODULE, PROJECT_DIR
    args := action
        . " --robot-hand-mode-on " . (robotHandModeOn ? "1" : "0")
        . " --enabled " . (enabled ? "1" : "0")
        . " --mode-state-on " . (RobotHandModeState() = "1" ? "1" : "0")
        . " --omni-paused " . (omniPausedOn ? "1" : "0")
        . " --plan-file " . Q(planPath)
    cmd := Q(ROBOT_HAND_PY) . " -m " . CONTROLLER_ROBOT_HAND_MODULE . " " . args
    if (RunWait(cmd, PROJECT_DIR, "Hide") != 0)
        return ""
    return LoadRobotHandActionPlan(planPath)
}

RunControllerOmniPauseAction(action, omniPausedOn, robotHandModeOn, skipPrimaryResume, planPath) {
    global ROBOT_HAND_PY, CONTROLLER_OMNIPAUSE_MODULE, PROJECT_DIR
    args := action
        . " --omni-paused " . (omniPausedOn ? "1" : "0")
        . " --robot-hand-mode-on " . (robotHandModeOn ? "1" : "0")
        . " --skip-primary-resume " . (skipPrimaryResume ? "1" : "0")
        . " --plan-file " . Q(planPath)
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

LaunchDashboardApp() {
    global ROBOT_HAND_PY, DASHBOARD_MODULE, CONTROLLER_MANIFEST_PATH
    return RunApp(ROBOT_HAND_PY, "-m " . DASHBOARD_MODULE . " " . Q(CONTROLLER_MANIFEST_PATH))
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

ProcessDashboardCommand() {
    global DASHBOARD_CMD_FILE, VLC2_PORT, VLC3_PORT
    if !FileExist(DASHBOARD_CMD_FILE)
        return
    try {
        action := Trim(FileRead(DASHBOARD_CMD_FILE, "UTF-8"))
        FileDelete(DASHBOARD_CMD_FILE)
    } catch {
        return
    }
    if (action = "")
        return
    switch action {
        case "portrait_prev":
            CancelLock(2), VlcHttpCmd(VLC2_PORT, "pl_previous")
        case "portrait_next":
            CancelLock(2), VlcHttpCmd(VLC2_PORT, "pl_next")
        case "portrait_lock":
            ToggleLock(2)
        case "portrait_trash":
            Discard(2)
        case "primary_prev":
            HandlePrevAction()
        case "primary_next":
            HandleNextAction()
        case "quarter_button":
            QueueRobotHandOffsetQuarterCycle()
        case "landscape_prev":
            CancelLock(3), VlcHttpCmd(VLC3_PORT, "pl_previous")
        case "landscape_next":
            CancelLock(3), VlcHttpCmd(VLC3_PORT, "pl_next")
        case "landscape_lock":
            ToggleLock(3)
        case "landscape_trash":
            Discard(3)
        case "link_toggle":
            ToggleRobotHandEnabled()
    }
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

GetRobotHandRect(&x, &y, &w, &h) {
    plan := ""
    GetCurrentWindowLayout(&plan)
    x := plan["robot_hand"]["x"]
    y := plan["robot_hand"]["y"]
    w := plan["robot_hand"]["w"]
    h := plan["robot_hand"]["h"]
}

SendToPid(pid, keys) {
    try ControlSend(keys, , "ahk_pid " pid)
}

SendToTitle(title, keys) {
    try ControlSend(keys, , title)
}

OpenPrimaryVlcFileDialog() {
    global pid1
    try {
        WinActivate("ahk_pid " pid1)
        WinWaitActive("ahk_pid " pid1, , 0.5)
        Sleep 50
        SendEvent("^o")
    }
}

OpenPrimaryVlcFileDialogWithManagedOmniPause() {
    global pid1, omniPaused

    shouldLeaveOmniPause := !omniPaused
    if (shouldLeaveOmniPause)
        EnterOmniPause()

    try {
        OpenPrimaryVlcFileDialog()

        if (shouldLeaveOmniPause) {
            dialogSpec := "ahk_class #32770 ahk_pid " pid1
            if WinWait(dialogSpec, , 1.0)
                WinWaitClose(dialogSpec)
        }
    } finally {
        if (shouldLeaveOmniPause)
            LeaveOmniPause(true)
    }
}

QueueRobotHandOffsetQuarterCycle() {
    global ROBOT_HAND_CMD_FILE
    WriteRawStateFile(ROBOT_HAND_CMD_FILE, "OFFSET_QUARTER_CYCLE")
}

HandlePrevAction() {
    global ROBOT_HAND_CMD_FILE, pid1
    if (EffectiveRobotHandModeState() = "1") {
        WriteRawStateFile(ROBOT_HAND_CMD_FILE, "PREV")
    } else {
        SendToPid(pid1, "p")
    }
}

HandleNextAction() {
    global ROBOT_HAND_CMD_FILE, pid1
    if (EffectiveRobotHandModeState() = "1") {
        WriteRawStateFile(ROBOT_HAND_CMD_FILE, "NEXT")
    } else {
        SendToPid(pid1, "n")
    }
}

ShowControllerLog(*) {
    global CONTROLLER_LOG_FILE
    Run('notepad.exe "' . CONTROLLER_LOG_FILE . '"')
}

HandleControllerExit(exitReason, exitCode) {
    global isShuttingDown
    if (isShuttingDown)
        return
    Log("Controller exiting unexpectedly reason=" . exitReason . " code=" . exitCode)
    ShutdownAll()
}

RobotHandModeState() {
    global ROBOT_HAND_MODE_FILE  ; if your variable is still ROBOT_MODE_HAND_FILE, use that name instead
    try {
        if !FileExist(ROBOT_HAND_MODE_FILE)
            return "0"
        return Trim(FileRead(ROBOT_HAND_MODE_FILE, "UTF-8"))
    } catch {
        return "0"
    }
}

RobotHandEnabled() {
    global ROBOT_HAND_ENABLED_FILE
    try {
        if !FileExist(ROBOT_HAND_ENABLED_FILE)
            return true
        return Trim(FileRead(ROBOT_HAND_ENABLED_FILE, "UTF-8")) != "0"
    } catch {
        return true
    }
}

SetRobotHandEnabled(enabled) {
    global ROBOT_HAND_ENABLED_FILE
    WriteRawStateFile(ROBOT_HAND_ENABLED_FILE, enabled ? "1" : "0")
}

SetRobotHandPaused(paused) {
    global ROBOT_HAND_PAUSED_FILE
    WriteRawStateFile(ROBOT_HAND_PAUSED_FILE, paused ? "1" : "0")
}

SetRobotHandAudioPaused(paused) {
    global AUDIO_PAUSED_FILE
    WriteRawStateFile(AUDIO_PAUSED_FILE, paused ? "1" : "0")
}

GetFunTimeDashboardRect(&x, &y, &w, &h) {
    plan := ""
    GetCurrentWindowLayout(&plan)
    x := plan["dashboard"]["x"]
    y := plan["dashboard"]["y"]
    w := plan["dashboard"]["w"]
    h := plan["dashboard"]["h"]
}

IsBrokerRunning() {
    try {
        wmi := ComObjGet("winmgmts:")
        query := "SELECT Name, CommandLine FROM Win32_Process WHERE "
            . "Name='python.exe' OR Name='pythonw.exe' OR Name='py.exe' OR "
            . "Name='powershell.exe' OR Name='pwsh.exe' OR Name='wscript.exe'"
        for process in wmi.ExecQuery(query) {
            name := ""
            cmdLine := ""
            try name := StrLower(process.Name . "")
            try cmdLine := StrLower(process.CommandLine . "")
            if ((name = "python.exe" || name = "pythonw.exe" || name = "py.exe") && InStr(cmdLine, "fun_time.broker_app"))
                return true
            if ((name = "powershell.exe" || name = "pwsh.exe" || name = "wscript.exe")
                && (InStr(cmdLine, "broker_tray.ps1") || InStr(cmdLine, "launch_broker_tray.vbs")))
                return true
        }
        return false
    } catch {
        return false
    }
}

IsProcessAlive(pid) {
    return pid && ProcessExist(pid)
}

IsVlcResponsive(port) {
    xml := VlcHttpReq(port, "/requests/status.xml", &st)
    return st = 200 && InStr(xml, "<state>")
}

IsMfpConnected() {
    global pidM, PRIMARY_VLC_PORT
    return IsProcessAlive(pidM) && IsVlcResponsive(PRIMARY_VLC_PORT) && IsBrokerRunning()
}

GetDashboardStatusSnapshot(&brokerRunning, &mfpConnected) {
    global dashboardStatusRefreshTick, dashboardBrokerRunning, dashboardMfpConnected
    global pidM, PRIMARY_VLC_PORT

    if (dashboardStatusRefreshTick = 0 || (A_TickCount - dashboardStatusRefreshTick) >= 2000) {
        brokerRunningNow := IsBrokerRunning()
        dashboardBrokerRunning := brokerRunningNow
        dashboardMfpConnected := IsProcessAlive(pidM) && IsVlcResponsive(PRIMARY_VLC_PORT) && brokerRunningNow
        dashboardStatusRefreshTick := A_TickCount
    }

    brokerRunning := dashboardBrokerRunning
    mfpConnected := dashboardMfpConnected
}

UpdateFunTimeDashboard() {
    global robotHandMode, fModeEnabled, locked2, locked3
    global PRIMARY_VLC_PORT, VLC2_PORT, VLC3_PORT

    primaryPath := GetCurrentFilePath(PRIMARY_VLC_PORT)
    portraitPath := GetCurrentFilePath(VLC2_PORT)
    landscapePath := GetCurrentFilePath(VLC3_PORT)
    osr2Auto := RobotHandModeState() = "1"
    robotHandEnabledNow := RobotHandEnabled()
    GetDashboardStatusSnapshot(&brokerRunningNow, &mfpConnectedNow)
    primaryUsesRobotHand := robotHandMode && robotHandEnabledNow
    GetFunTimeDashboardRect(&x, &y, &w, &h)
    WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, brokerRunningNow, mfpConnectedNow, x, y, w, h, locked2, locked3)
}

WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabled, brokerRunning, mfpConnected, x, y, w, h, portraitLocked, landscapeLocked) {
    global DASHBOARD_STATE_FILE
    global fModeEnabled, lastDashboardSnapshotText

    snapshotText := "[window]`n"
        . "x=" . x . "`n"
        . "y=" . y . "`n"
        . "width=" . w . "`n"
        . "height=" . h . "`n"
        . "[broker]`n"
        . "running=" . (brokerRunning ? "1" : "0") . "`n"
        . "[controller]`n"
        . "running=1`n"
        . "[fmode]`n"
        . "enabled=" . (fModeEnabled ? "1" : "0") . "`n"
        . "[robot_link]`n"
        . "enabled=" . (robotHandEnabled ? "1" : "0") . "`n"
        . "[osr2]`n"
        . "mode=" . (osr2Auto ? "auto" : "controlled") . "`n"
        . "[mfp]`n"
        . "connected=" . (mfpConnected ? "1" : "0") . "`n"
        . "[primary]`n"
        . "uses_robot_hand=" . (primaryUsesRobotHand ? "1" : "0") . "`n"
        . "path=" . IniEscape(primaryPath) . "`n"
        . "locked=0`n"
        . "[portrait]`n"
        . "path=" . IniEscape(portraitPath) . "`n"
        . "locked=" . (portraitLocked ? "1" : "0") . "`n"
        . "[landscape]`n"
        . "path=" . IniEscape(landscapePath) . "`n"
        . "locked=" . (landscapeLocked ? "1" : "0") . "`n"

    if (snapshotText = lastDashboardSnapshotText)
        return
    lastDashboardSnapshotText := snapshotText
    FileDelete(DASHBOARD_STATE_FILE)
    FileAppend(snapshotText, DASHBOARD_STATE_FILE, "UTF-16")
}

IniEscape(value) {
    text := value . ""
    text := StrReplace(text, "`r", " ")
    text := StrReplace(text, "`n", " ")
    return text
}

EnforceRobotHandOutputs(active, isTransition := false) {
    global pid1

    if (active) {
        EnsurePrimaryVlcPlayback(false)
        SetRobotHandPaused(false)
        SetRobotHandAudioPaused(false)
        if (isTransition) {
            try WinShow("Robot Hand")
            try WinSetAlwaysOnTop(false, "ahk_pid " pid1)
            try WinSetAlwaysOnTop(true, "Robot Hand")
            try WinActivate("Robot Hand")
        }
    } else {
        SetRobotHandPaused(true)
        SetRobotHandAudioPaused(true)
        if (isTransition) {
            try WinHide("Robot Hand")
            try WinSetAlwaysOnTop(false, "Robot Hand")
            try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
        }
        EnsurePrimaryVlcPlayback(true)
    }
}

EffectiveRobotHandModeState() {
    if (!RobotHandEnabled())
        return "0"
    return RobotHandModeState()
}

SyncRobotHandState() {
    global robotHandMode, pid1, omniPaused

    if (omniPaused)
        return

    modeState := EffectiveRobotHandModeState()
    modeOn := (modeState = "1")

    if (modeOn && !robotHandMode) {
        robotHandMode := true
        Log("Entering Robot Hand mode")
        EnforceRobotHandOutputs(true, true)
    } else if (!modeOn && robotHandMode) {
        robotHandMode := false
        Log("Leaving Robot Hand mode")
        EnforceRobotHandOutputs(false, true)
    } else {
        EnforceRobotHandOutputs(modeOn, false)
    }
}

ToggleRobotHandEnabled() {
    global robotHandMode, omniPaused
    planPath := BuildRobotHandPlanPath()
    plan := RunControllerRobotHandAction("toggle-enabled", robotHandMode, RobotHandEnabled(), omniPaused, planPath)
    if !IsObject(plan)
        return
    if (plan["write_enabled"])
        SetRobotHandEnabled(plan["enabled_value"])
    if (plan["log_message"] != "")
        Log(plan["log_message"])
    robotHandMode := plan["next_robot_hand_mode"]
    if (plan["enforce_outputs"])
        EnforceRobotHandOutputs(plan["enforce_active"], plan["is_transition"])
    UpdateFunTimeDashboard()
}

BuildPlaylistFilePath(name) {
    global STATE_DIR
    return STATE_DIR . "\" . name . ".m3u"
}

WriteFModePlaylists(enabled) {
    global PRIMARY_VLC_SOURCES, PORTRAIT_DIR, LANDSCAPE_DIR, FAVS_FILE, STATE_DIR

    args := "write-fmode-playlists"
        . " --primary-sources " . Q(PRIMARY_VLC_SOURCES)
        . " --portrait-sources " . Q(PORTRAIT_DIR)
        . " --landscape-sources " . Q(LANDSCAPE_DIR)
        . " --favs-file " . Q(FAVS_FILE)
        . " --state-dir " . Q(STATE_DIR)
        . " --enabled " . (enabled ? "1" : "0")

    return RunControllerModesAction(args) = 0
}

ReplaceVlcPlaylistFromFile(port, playlistPath, repeatMode := "") {
    global VLC_PASS
    args := "replace-playlist"
        . " --port " . port
        . " --password " . Q(VLC_PASS)
        . " --playlist-path " . Q(playlistPath)
    if (repeatMode != "")
        args .= " --repeat-mode " . repeatMode
    return RunControllerVlcAction(args) = 0
}

ApplyFModePlaylists(enabled) {
    global PRIMARY_VLC_PORT, VLC2_PORT, VLC3_PORT, locked2, locked3

    if !WriteFModePlaylists(enabled) {
        Log("F-mode toggle aborted because one or more playlists would be empty")
        return false
    }

    locked2 := false
    locked3 := false

    if !ReplaceVlcPlaylistFromFile(PRIMARY_VLC_PORT, BuildPlaylistFilePath("primary_vlc_playlist"))
        return false
    if !ReplaceVlcPlaylistFromFile(VLC2_PORT, BuildPlaylistFilePath("portrait_vlc_playlist"), "all")
        return false
    if !ReplaceVlcPlaylistFromFile(VLC3_PORT, BuildPlaylistFilePath("landscape_vlc_playlist"), "all")
        return false
    return true
}

ToggleFMode() {
    global fModeEnabled

    targetEnabled := !fModeEnabled
    if !ApplyFModePlaylists(targetEnabled) {
        Log("F-mode hotkey: unchanged")
        return
    }

    fModeEnabled := targetEnabled
    Log("F-mode hotkey: " . (fModeEnabled ? "enabled" : "disabled"))
    UpdateFunTimeDashboard()
}

; -------------------- LAUNCH --------------------

Log("Controller starting")
if FileExist(ICON_PATH)
    TraySetIcon(ICON_PATH)

OnExit(HandleControllerExit)

SetRobotHandEnabled(true)
SetRobotHandPaused(true)
SetRobotHandAudioPaused(true)
RestartBroker()

pid1 := RunVLC(Join(
    "--no-one-instance --random --repeat",
    "--extraintf http",
    "--http-host 127.0.0.1",
    "--http-port " . PRIMARY_VLC_PORT,
    "--http-password " . Q(VLC_PASS)
), PRIMARY_VLC_SOURCES)
WaitForHttp(PRIMARY_VLC_PORT, 7000)
Sleep 300
SendToPid(pid1, "n")

pidM := RunApp(MFP_EXE, "")
WinWait("ahk_pid " pidM, , 15)
Sleep 5000

pid2 := RunVLC(Join(
    "--no-one-instance --random --loop",
    "--extraintf http",
    "--http-host 127.0.0.1",
    "--http-port " . VLC2_PORT,
    "--http-password " . Q(VLC_PASS)
), PORTRAIT_DIR)

pid3 := RunVLC(Join(
    "--no-one-instance --random --loop",
    "--extraintf http",
    "--http-host 127.0.0.1",
    "--http-port " . VLC3_PORT,
    "--http-password " . Q(VLC_PASS)
), LANDSCAPE_DIR)

WaitForHttp(VLC2_PORT, 7000)
WaitForHttp(VLC3_PORT, 7000)

SetRepeatMode(VLC2_PORT, "all")
SetRepeatMode(VLC3_PORT, "all")

Sleep 250
VlcHttpCmd(VLC2_PORT, "pl_next")
Sleep 150
VlcHttpCmd(VLC3_PORT, "pl_next")

PrepareRandomFavsBrowserManifest()

PositionAll(pid1, pid2, pid3, pidM)
SetTopMost(pid1, pid2, pid3, pidM)
MaybeLaunchRandomFavsBrowser(pidM)
try FileDelete(DASHBOARD_CMD_FILE)
pidD := LaunchDashboardApp()
SetTimer(UpdateFunTimeDashboard, 500)
SetTimer(ProcessDashboardCommand, 150)
UpdateFunTimeDashboard()

Sleep 1200

rx := 0, ry := 0, rw := 0, rh := 0
GetRobotHandRect(&rx, &ry, &rw, &rh)

pidR := RunApp(ROBOT_HAND_PY
    , "-m " . ROBOT_HAND_MODULE
    . " --config " . Q(CONFIG_PATH)
    . " --clips-folder " . Q(ROBOT_HAND_CLIPS)
    . " --x " . rx
    . " --y " . ry
    . " --width " . rw
    . " --height " . rh
)
Log("Started Robot Hand listener pid=" . pidR)

pidA := RunApp(ROBOT_HAND_PY
    , "-m " . ROBOT_HAND_AUDIO_MODULE
    . " --config " . Q(CONFIG_PATH)
    . " --audio-folder " . Q(ROBOT_HAND_AUDIO)
)
Log("Started Robot Hand audio pid=" . pidA)

SetTimer(SyncRobotHandState, 200)

A_IconTip := "Fun Time Controller"
A_TrayMenu.Delete()
A_TrayMenu.Add("Open Controller Log", ShowControllerLog)
A_TrayMenu.Add()
A_TrayMenu.Add("Exit Fun Time", (*) => ShutdownAll())
A_TrayMenu.AddStandard()

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
    VlcHttpCmd(VLC2_PORT, "pl_previous")
}
Right::{
    CancelLock(2)
    VlcHttpCmd(VLC2_PORT, "pl_next")
}
Up::Discard(2)
Down::ToggleLock(2)

a::{
    CancelLock(3)
    VlcHttpCmd(VLC3_PORT, "pl_previous")
}
d::{
    CancelLock(3)
    VlcHttpCmd(VLC3_PORT, "pl_next")
}
w::Discard(3)
s::ToggleLock(3)

; =====================================================================
; ========================= IMPLEMENTATION ============================
; =====================================================================

ShutdownAll() {
    global isShuttingDown, pid1, pid2, pid3, pidM, pidD, pidR, pidA
    if (isShuttingDown)
        return
    isShuttingDown := true
    Log("Shutdown requested")
    SetTimer(SyncRobotHandState, 0)
    SetTimer(UpdateFunTimeDashboard, 0)
    SetTimer(ProcessDashboardCommand, 0)

    for pid in [pid1, pid2, pid3, pidM, pidD, pidR, pidA]
        TryClosePid(pid)

    Sleep 700

    for pid in [pid1, pid2, pid3, pidM, pidD, pidR, pidA]
        TryKillPid(pid)

    Sleep 300

    for pid in [pid1, pid2, pid3, pidM, pidD, pidR, pidA]
        ForceKillPid(pid)

    ExitApp
}

RestartBroker() {
    global PROJECT_DIR

    launchPath := PROJECT_DIR . "\launch_broker_tray.vbs"
    psCmd := "$targets = Get-CimInstance Win32_Process | Where-Object { "
        . "(($_.Name -match '^pythonw?\.exe$|^py\.exe$') -and $_.CommandLine -match 'fun_time\.broker_app') -or "
        . "(($_.Name -match '^powershell\.exe$|^pwsh\.exe$|^wscript\.exe$') -and $_.CommandLine -match 'broker_tray\.ps1|launch_broker_tray\.vbs') "
        . "}; "
        . "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
        . "Start-Sleep -Milliseconds 400"

    try RunWait("powershell.exe -NoProfile -WindowStyle Hidden -Command " . Q(psCmd), PROJECT_DIR, "Hide")
    Sleep 400
    if FileExist(launchPath)
        Run(Q("wscript.exe") . " " . Q(launchPath), PROJECT_DIR)
}

PositionAll(pid1, pid2, pid3, pidM) {
    GetActualMfpSize(&mfpW, &mfpH)
    plan := ""
    GetCurrentWindowLayout(&plan, mfpW, mfpH)
    MovePidWindow(pid2, plan["portrait"]["x"], plan["portrait"]["y"], plan["portrait"]["w"], plan["portrait"]["h"])
    MovePidWindow(pid1, plan["primary"]["x"], plan["primary"]["y"], plan["primary"]["w"], plan["primary"]["h"])
    MovePidWindow(pid3, plan["landscape"]["x"], plan["landscape"]["y"], plan["landscape"]["w"], plan["landscape"]["h"])
    PositionMfpWindow(pidM)
}

PositionMfpWindow(pidM) {
    hwnd := WinWait("ahk_pid " pidM, , 10)

    GetActualMfpSize(&moveW, &moveH)
    plan := ""
    GetCurrentWindowLayout(&plan, moveW, moveH)
    moveX := plan["mfp"]["x"]
    moveY := plan["mfp"]["y"]

    Loop 3 {
        WinRestore("ahk_id " hwnd)
        WinMove(moveX, moveY, moveW, moveH, "ahk_id " hwnd)
        Sleep 80
        WinGetPos(&actualX, &actualY, &actualW, &actualH, "ahk_id " hwnd)
        GetCurrentWindowLayout(&plan, actualW, actualH)
        deltaX := plan["mfp"]["x"] - actualX
        deltaY := plan["mfp"]["y"] - actualY
        if (Abs(deltaX) <= 1 && Abs(deltaY) <= 1)
            break
        moveX += deltaX
        moveY += deltaY
        moveW := actualW
        moveH := actualH
    }
}

GetActualMfpSize(&w, &h) {
    global pidM, LANDSCAPE_WIDTH_RATIO, MFP_WIDTH_RATIO, MFP_HEIGHT_RATIO
    hwnd := pidM ? WinExist("ahk_pid " pidM) : 0
    if (hwnd) {
        try {
            WinGetPos(, , &w, &h, "ahk_id " hwnd)
            if (w > 0 && h > 0)
                return
        }
    }

    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)
    landscapeW := Floor(mainRect["w"] * Clamp01(LANDSCAPE_WIDTH_RATIO))
    leftW := mainRect["w"] - landscapeW
    w := Floor(leftW * Clamp01(MFP_WIDTH_RATIO))
    h := Floor(mainRect["h"] * Clamp01(MFP_HEIGHT_RATIO))
}

GetRandomFavsBrowserRect(&x, &y, &w, &h) {
    plan := ""
    GetCurrentWindowLayout(&plan)
    x := plan["random_favs_browser"]["x"]
    y := plan["random_favs_browser"]["y"]
    w := plan["random_favs_browser"]["w"]
    h := plan["random_favs_browser"]["h"]
}

MovePidWindow(pid, x, y, w, h) {
    hwnd := WinWait("ahk_pid " pid, , 10)
    WinRestore("ahk_id " hwnd)
    WinMove(x, y, w, h, "ahk_id " hwnd)
}

SetTopMost(pid1, pid2, pid3, pidM) {
    for pid in [pid1, pid2, pid3, pidM] {
        try {
            hwnd := WinExist("ahk_pid " pid)
            if (hwnd) {
                WinSetAlwaysOnTop(true, "ahk_id " hwnd)
                WinActivate("ahk_id " hwnd)
            }
        }
    }
}

PrepareRandomFavsBrowserManifest() {
    global ROBOT_HAND_PY, RANDOM_FAVS_BROWSER_MANIFEST_FILE, CONFIG_PATH
    try FileDelete(RANDOM_FAVS_BROWSER_MANIFEST_FILE)
    cmd := Q(ROBOT_HAND_PY)
        . " -m fun_time.random_favs_browser"
        . " --config " . Q(CONFIG_PATH)
        . " --output " . Q(RANDOM_FAVS_BROWSER_MANIFEST_FILE)
    try RunWait(cmd, PROJECT_DIR, "Hide")
}

MaybeLaunchRandomFavsBrowser(pidM) {
    global RANDOM_FAVS_BROWSER_MANIFEST_FILE, RANDOM_FAVS_BROWSER_SHORTCUT_PATH

    manifest := ReadRandomFavsBrowserManifest(RANDOM_FAVS_BROWSER_MANIFEST_FILE)
    if (manifest.profileDir = "" || manifest.urls.Length = 0)
        return

    existing := GetVisibleChromeWindowHandles()
    launchSpec := BuildRandomFavsBrowserLaunchSpec(manifest)
    if (launchSpec.cmd = "") {
        Log("Random Favs Browser skipped because the browser shortcut could not be resolved")
        return
    }
    try Run(launchSpec.cmd, launchSpec.workDir, , &browserPid)

    newHwnd := WaitForNewChromeWindow(existing, 8000)
    if (!newHwnd) {
        Log("Random Favs Browser skipped because the browser launch command did not produce a new visible window")
        return
    }

    GetRandomFavsBrowserRect(&x, &y, &w, &h)
    try {
        WinRestore("ahk_id " newHwnd)
        WinMove(x, y, w, h, "ahk_id " newHwnd)
        WinSetAlwaysOnTop(false, "ahk_id " newHwnd)
    }
    try {
        WinSetAlwaysOnTop(true, "ahk_pid " pidM)
        WinActivate("ahk_pid " pidM)
    }
    Log("Random Favs Browser positioned using direct launch for profile " . manifest.profileDir)
}

BuildRandomFavsBrowserLaunchSpec(manifest) {
    global RANDOM_FAVS_BROWSER_SHORTCUT_PATH

    target := "", workDir := "", args := "", description := "", iconPath := "", iconNum := 0, runState := 0
    try FileGetShortcut(RANDOM_FAVS_BROWSER_SHORTCUT_PATH, &target, &workDir, &args, &description, &iconPath, &iconNum, &runState)
    if (target = "")
        return {cmd: "", workDir: ""}

    cmd := Q(target)
    existingArgs := Trim(args)
    if (existingArgs != "")
        cmd .= " " . existingArgs
    if (manifest.profileDir != "" && !InStr(StrLower(existingArgs), "--profile-directory"))
        cmd .= " --profile-directory=" . Q(manifest.profileDir)
    if !InStr(StrLower(existingArgs), "--new-window")
        cmd .= " --new-window"
    for url in manifest.urls
        cmd .= " " . Q(url)
    return {cmd: cmd, workDir: workDir}
}

ReadRandomFavsBrowserManifest(path) {
    result := {profileDir: "", urls: []}
    if !FileExist(path)
        return result
    content := ""
    try content := FileRead(path, "UTF-8")
    if (content = "")
        return result
    lines := StrSplit(content, "`n", "`r")
    if (lines.Length >= 1)
        result.profileDir := Trim(lines[1])
    Loop lines.Length - 1 {
        url := Trim(lines[A_Index + 1])
        if (url != "")
            result.urls.Push(url)
    }
    return result
}

GetVisibleChromeWindowHandles() {
    handles := []
    winList := WinGetList("ahk_exe chrome.exe")
    for hwnd in winList {
        title := ""
        try title := WinGetTitle("ahk_id " hwnd)
        if (Trim(title) = "")
            continue
        handles.Push(hwnd)
    }
    return handles
}

WaitForNewChromeWindow(existingHandles, timeoutMs := 8000) {
    started := A_TickCount
    loop {
        current := GetVisibleChromeWindowHandles()
        for hwnd in current {
            if !HandleInList(hwnd, existingHandles)
                return hwnd
        }
        if (A_TickCount - started > timeoutMs)
            break
        Sleep 200
    }
    return 0
}

HandleInList(hwnd, handles) {
    for existing in handles {
        if (existing = hwnd)
            return true
    }
    return false
}

; -------------------- HTTP --------------------

Base64EncodeUtf8(s) {
    byteLen := StrPut(s, "UTF-8") - 1
    buf := Buffer(byteLen, 0)
    StrPut(s, buf, "UTF-8")
    DllCall("Crypt32\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", byteLen, "UInt", 0x1, "Ptr", 0, "UIntP", &outChars := 0)
    out := Buffer(outChars * 2, 0)
    DllCall("Crypt32\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", byteLen, "UInt", 0x1, "Ptr", out.Ptr, "UIntP", &outChars)
    return StrGet(out, outChars, "UTF-16")
}

VlcHttpReq(port, path, &status := 0) {
    status := 0
    try {
        req := ComObject("WinHttp.WinHttpRequest.5.1")
        url := "http://127.0.0.1:" . port . path
        req.Open("GET", url, false)

        cred := VLC_USER . ":" . VLC_PASS
        auth := "Basic " . Base64EncodeUtf8(cred)
        req.SetRequestHeader("Authorization", auth)

        req.Send()
        status := req.Status
        return req.ResponseText
    } catch {
        return ""
    }
}

WaitForHttp(port, timeoutMs := 5000) {
    global VLC_PASS
    args := "wait-for-http"
        . " --port " . port
        . " --password " . Q(VLC_PASS)
        . " --timeout-ms " . timeoutMs
    if (RunControllerVlcAction(args) = 0)
        return true
    Log("VLC HTTP interface failed to come up on port " . port)
    MsgBox("VLC HTTP interface did not come up on port " . port . "`nControls for that player will not work until this is resolved.", "fun_time", "Icon!")
    return false
}

VlcHttpCmd(port, cmd) {
    VlcHttpReq(port, "/requests/status.xml?command=" . cmd, &st)
}

EnsurePrimaryVlcPlayback(shouldPlay) {
    global PRIMARY_VLC_PORT, VLC_PASS
    args := "ensure-playback-state"
        . " --port " . PRIMARY_VLC_PORT
        . " --password " . Q(VLC_PASS)
        . " --should-play " . (shouldPlay ? "1" : "0")
    if (RunControllerVlcAction(args) = 0)
        return true
    Log("Primary VLC failed to reach playback state " . (shouldPlay ? "playing" : "paused"))
    return false
}

SetRepeatMode(port, target) {
    global VLC_PASS
    args := "set-repeat-mode"
        . " --port " . port
        . " --password " . Q(VLC_PASS)
        . " --target " . target
    return RunControllerVlcAction(args) = 0
}

CancelLock(which) {
    global locked2, locked3
    port := (which = 2) ? VLC2_PORT : VLC3_PORT
    currentLocked := (which = 2) ? locked2 : locked3
    planPath := BuildLockPlanPath(which)
    plan := RunControllerLockAction("cancel-lock", which, currentLocked, "", planPath)
    if !IsObject(plan)
        return

    if (plan["repeat_mode"] != "")
        SetRepeatMode(port, plan["repeat_mode"])
    if (which = 2)
        locked2 := plan["next_locked"]
    else
        locked3 := plan["next_locked"]
}

; -------------------- Current item --------------------

GetCurrentFilePath(port) {
    global VLC_PASS
    outputPath := BuildVlcQueryOutputPath("vlc_current_file")
    args := "current-file-path"
        . " --port " . port
        . " --password " . Q(VLC_PASS)
        . " --output-file " . Q(outputPath)
    if (RunControllerVlcAction(args) != 0)
        return ""
    try {
        if !FileExist(outputPath)
            return ""
        return Trim(FileRead(outputPath, "UTF-8"))
    } finally {
        try FileDelete(outputPath)
    }
}

EnsureInFavs(fullPath) {
    RunMediaAction("ensure-in-favs", fullPath)
}

RemoveFromFavs(fullPath) {
    RunMediaAction("remove-from-favs", fullPath)
}

; -------------------- Weird move + actions --------------------

MoveToWeird(srcPath) {
    RunMediaAction("move-to-weird", srcPath)
}

Discard(which) {
    global locked2, locked3
    port := (which = 2) ? VLC2_PORT : VLC3_PORT
    src := GetCurrentFilePath(port)
    currentLocked := (which = 2) ? locked2 : locked3
    planPath := BuildLockPlanPath(which)
    plan := RunControllerLockAction("discard", which, currentLocked, src, planPath)
    if !IsObject(plan)
        return

    if (plan["log_message"] != "")
        Log(plan["log_message"])
    if (plan["repeat_mode"] != "")
        SetRepeatMode(port, plan["repeat_mode"])
    if (which = 2)
        locked2 := plan["next_locked"]
    else
        locked3 := plan["next_locked"]
    if (plan["remove_from_favs"])
        RemoveFromFavs(src)
    if (plan["advance_playlist"]) {
        VlcHttpCmd(port, "pl_next")
        Sleep 250
    }
    if (plan["move_to_weird"])
        MoveToWeird(src)
}

ToggleLock(which) {
    global locked2, locked3
    port := (which = 2) ? VLC2_PORT : VLC3_PORT
    currentLocked := (which = 2) ? locked2 : locked3
    currentPath := GetCurrentFilePath(port)
    planPath := BuildLockPlanPath(which)
    plan := RunControllerLockAction("toggle-lock", which, currentLocked, currentPath, planPath)
    if !IsObject(plan)
        return

    if (plan["repeat_mode"] != "")
        SetRepeatMode(port, plan["repeat_mode"])
    if (plan["ensure_in_favs"])
        EnsureInFavs(currentPath)
    if (plan["advance_playlist"])
        VlcHttpCmd(port, "pl_next")
    if (which = 2)
        locked2 := plan["next_locked"]
    else
        locked3 := plan["next_locked"]
    if (plan["log_message"] != "")
        Log(plan["log_message"])
}

; -------------------- OmniPause --------------------

WriteCmd(file, cmd) {
    WriteRawStateFile(file, cmd)
}

OmniPauseToggle() {
    global omniPaused, robotHandMode
    planPath := BuildOmniPausePlanPath()
    plan := RunControllerOmniPauseAction("toggle", omniPaused, robotHandMode, false, planPath)
    if !IsObject(plan)
        return
    if (plan["action"] = "enter")
        EnterOmniPause()
    else
        LeaveOmniPause()
}

EnterOmniPause() {
    global omniPaused, robotHandMode, pid1, pid2, pid3, pidM, pidD
    global VLC2_PORT, VLC3_PORT
    planPath := BuildOmniPausePlanPath()
    plan := RunControllerOmniPauseAction("enter", omniPaused, robotHandMode, false, planPath)
    if !IsObject(plan)
        return

    omniPaused := plan["next_omni_paused"]
    Log(plan["log_message"])

    if (plan["robot_hand_branch"]) {
        ; Auto mode: VLC1 is already paused by Robot Hand mode; pause VLC2+3, freeze Robot Hand, and pause audio
        VlcHttpCmd(VLC2_PORT, "pl_pause")
        VlcHttpCmd(VLC3_PORT, "pl_pause")
        SetRobotHandPaused(true)
        SetRobotHandAudioPaused(true)
        try WinSetAlwaysOnTop(false, "Robot Hand")
    } else {
        ; Controlled mode: pause all 3 VLCs
        EnsurePrimaryVlcPlayback(false)
        VlcHttpCmd(VLC2_PORT, "pl_pause")
        VlcHttpCmd(VLC3_PORT, "pl_pause")
    }

    ; Remove always-on-top from all Fun Time windows so they stop blocking other windows.
    for pid in [pid1, pid2, pid3, pidM, pidD] {
        try WinSetAlwaysOnTop(false, "ahk_pid " pid)
    }

    Suspend true
}

LeaveOmniPause(skipPrimaryVlcPlaybackToggleOnResume := false) {
    global omniPaused, robotHandMode, pid1, pid2, pid3, pidM, pidD
    global VLC2_PORT, VLC3_PORT
    planPath := BuildOmniPausePlanPath()
    plan := RunControllerOmniPauseAction("leave", omniPaused, robotHandMode, skipPrimaryVlcPlaybackToggleOnResume, planPath)
    if !IsObject(plan)
        return

    Log(plan["log_message"])
    Suspend false

    if (plan["robot_hand_branch"]) {
        ; Auto mode: resume Robot Hand animation, resume audio, and resume VLC2+3
        SetRobotHandPaused(false)
        SetRobotHandAudioPaused(false)
        VlcHttpCmd(VLC2_PORT, "pl_pause")  ; toggle back to playing
        VlcHttpCmd(VLC3_PORT, "pl_pause")
    } else {
        ; Controlled mode: resume all 3 VLCs and restore VLC1 always-on-top.
        if (plan["resume_primary_playback"])
            EnsurePrimaryVlcPlayback(true)
        VlcHttpCmd(VLC2_PORT, "pl_pause")  ; toggle back to playing
        VlcHttpCmd(VLC3_PORT, "pl_pause")
        try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
    }

    ; Restore always-on-top for the dashboard, secondary VLC windows, and MFP.
    try WinSetAlwaysOnTop(true, "ahk_pid " pidD)
    try WinSetAlwaysOnTop(true, "ahk_pid " pid2)
    try WinSetAlwaysOnTop(true, "ahk_pid " pid3)
    try WinSetAlwaysOnTop(true, "ahk_pid " pidM)

    ; Allow SyncRobotHandState to run again and handle any mode transitions that
    ; occurred while paused (e.g. OSR2 exited freemode after receiving neutral pos)
    omniPaused := plan["next_omni_paused"]
    SyncRobotHandState()

    ; If still in auto mode after the sync check, restore Robot Hand always-on-top.
    if (robotHandMode) {
        try WinSetAlwaysOnTop(true, "Robot Hand")
        try WinActivate("Robot Hand")
    }
}
