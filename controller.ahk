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
MEDIA_ACTIONS_MODULE := RequireManifestValue("modules", "media_actions_module")
CONTROLLER_MODES_MODULE := RequireManifestValue("modules", "controller_modes_module")
CONTROLLER_LOCK_MODULE := RequireManifestValue("modules", "controller_lock_module")
CONTROLLER_ROBOT_HAND_MODULE := RequireManifestValue("modules", "controller_robot_hand_module")
CONTROLLER_OMNIPAUSE_MODULE := RequireManifestValue("modules", "controller_omnipause_module")
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
CHROME_SHORTCUT_PATH := RequireManifestValue("chrome_overlay", "shortcut_path")
CHROME_MANIFEST_FILE := RequireManifestValue("chrome_overlay", "manifest_file")
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
pidR := 0
pidA := 0
dashboardLastX := ""
dashboardLastY := ""
dashboardLastW := ""
dashboardLastH := ""
dashboardStatusRefreshTick := 0
dashboardBrokerRunning := false
dashboardMfpConnected := false
funTimeDashboardGui := ""
funTimeDashboardControls := Map()
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

ToFileUri(winPath) {
    if (winPath = "")
        return ""
    p := StrReplace(winPath, "\", "/")
    p := StrReplace(p, " ", "%20")
    return "file:///" . p
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

GetRobotHandRect(&x, &y, &w, &h) {
    global PRIMARY_TOP_RATIO
    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)
    secondaryL := secondaryRect["x"]
    secondaryT := secondaryRect["y"]
    secondaryW := secondaryRect["w"]
    secondaryH := secondaryRect["h"]
    portraitH := Floor(secondaryH * Clamp01(PRIMARY_TOP_RATIO))
    primaryH := secondaryH - portraitH

    x := secondaryL
    y := secondaryT + portraitH
    w := secondaryW
    h := primaryH
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
    layout := ""
    GetDashboardMonitorPreviewLayout(&layout)
    GetActualMfpSize(&mfpW, &mfpH)
    stack := ""
    GetLeftPartitionStackLayout(layout["dashboard_w"], layout["dashboard_h"], mfpW, mfpH, &stack)
    x := stack["dashboard_x"]
    y := stack["dashboard_y"]
    w := layout["dashboard_w"]
    h := layout["dashboard_h"]
}

AddDashboardText(guiObj, controls, key, options, text, clickHandler := "") {
    ctrl := guiObj.AddText(options, text)
    if (clickHandler != "")
        ctrl.OnEvent("Click", clickHandler)
    controls[key] := ctrl
    return ctrl
}

SetDashboardControlRect(ctrl, x, y, w, h) {
    ctrl.Move(Round(x), Round(y), Max(1, Round(w)), Max(1, Round(h)))
}

SetDashboardControlVisual(ctrl, text, bgColor, fgColor := COLOR_TEXT) {
    ctrl.Text := text
    ctrl.Opt("+Background" . bgColor)
    ctrl.Opt("+c" . fgColor)
}

GetDashboardMonitorPreviewLayout(&layout) {
    global PRIMARY_TOP_RATIO, LANDSCAPE_WIDTH_RATIO, MFP_WIDTH_RATIO, MFP_HEIGHT_RATIO

    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)

    outerPad := 10
    bottomPad := 6
    topY := outerPad
    monitorGap := 10
    previewMaxH := 250
    baseScale := previewMaxH / Max(mainRect["h"], secondaryRect["h"])

    leftW := Round(mainRect["w"] * baseScale)
    leftH := Round(mainRect["h"] * baseScale)
    rightW := Round(secondaryRect["w"] * baseScale)
    rightH := Round(secondaryRect["h"] * baseScale)
    mainX := outerPad
    secondaryX := mainX + leftW + monitorGap
    secondaryY := topY

    innerPad := 10
    panelGap := 8
    statusChipSize := 12
    statusChipGap := 1
    portraitUnits := 7
    primaryUnits := 4
    stackGap := 8

    rightInnerX := secondaryX + innerPad
    rightInnerY := secondaryY + innerPad
    rightInnerW := Max(40, rightW - innerPad * 2)
    rightInnerH := Max(40, rightH - innerPad * 2)
    availableStackH := Max(80, rightInnerH - stackGap)
    unitH := Max(10, Floor(availableStackH / (portraitUnits + primaryUnits)))
    portraitH := Max(52, unitH * portraitUnits)
    primaryH := Max(48, availableStackH - portraitH)
    portraitY := rightInnerY
    primaryY := rightInnerY + portraitH + stackGap

    mainY := portraitY + Floor((portraitH - leftH) / 2)
    previewBottom := Max(mainY + leftH, secondaryY + rightH, primaryY + primaryH)

    mainInnerX := mainX + innerPad
    mainInnerY := mainY + innerPad
    mainInnerW := Max(40, leftW - innerPad * 2)
    mainInnerH := Max(40, leftH - innerPad * 2)

    landscapeW := Max(34, Floor(mainInnerW * Clamp01(LANDSCAPE_WIDTH_RATIO)))
    leftStripW := Max(52, mainInnerW - landscapeW - panelGap)
    mfpMaxW := Max(44, Floor(leftStripW * Clamp01(MFP_WIDTH_RATIO)))
    statusStripY := mainInnerY
    statusStripH := statusChipSize + 6
    mfpAreaY := statusStripY + statusStripH + panelGap
    mfpAreaH := Max(28, mainInnerH - statusStripH - panelGap)
    mfpPreviewAspect := 0.67
    mfpH := Max(40, Floor(mfpAreaH * 0.92))
    mfpW := Min(mfpMaxW, Round(mfpH * mfpPreviewAspect))
    statusStripW := Max(mfpW, statusChipSize * 3 + statusChipGap * 2 + 8)
    leftColumnNudge := 2
    statusStripX := mainInnerX + Floor((leftStripW - statusStripW) / 2) - leftColumnNudge
    mfpX := mainInnerX + Floor((leftStripW - mfpW) / 2) - leftColumnNudge
    mfpY := mfpAreaY + Floor((mfpAreaH - mfpH) / 2)
    landscapeX := mainInnerX + leftStripW + panelGap
    landscapeY := mainInnerY

    mainRectPreview := Map("x", mainX, "y", mainY, "w", leftW, "h", leftH)
    secondaryRectPreview := Map("x", secondaryX, "y", secondaryY, "w", rightW, "h", rightH)
    landscapeRect := Map("x", landscapeX, "y", landscapeY, "w", landscapeW, "h", mainInnerH)
    portraitRect := Map("x", rightInnerX, "y", portraitY, "w", rightInnerW, "h", portraitH)
    primaryRect := Map("x", rightInnerX, "y", primaryY, "w", rightInnerW, "h", primaryH)
    osr2W := 56
    osr2H := 56
    linkW := 62
    linkGap := 8
    osr2X := secondaryX - osr2W - linkGap - linkW - linkGap
    osr2Y := primaryY + Floor((primaryH - osr2H) / 2)
    linkY := primaryY + Floor((primaryH - 18) / 2)
    linkX := osr2X + osr2W + linkGap
    statusRowX := statusStripX + Floor((statusStripW - (statusChipSize * 3 + statusChipGap * 2)) / 2)
    statusRowY := statusStripY + 3
    dashboardW := secondaryX + rightW + outerPad
    titleY := previewBottom - 14
    dashboardH := Max(previewBottom, osr2Y + osr2H, linkY + 18) + bottomPad

    layout := Map(
        "dashboard_w", dashboardW,
        "dashboard_h", dashboardH,
        "preview_bottom", previewBottom,
        "title", Map("x", outerPad, "y", titleY, "w", 88, "h", 12),
        "main_monitor", mainRectPreview,
        "secondary_monitor", secondaryRectPreview,
        "main_status_strip", Map("x", statusStripX, "y", statusStripY, "w", statusStripW, "h", statusStripH),
        "mfp_panel", Map("x", mfpX, "y", mfpY, "w", mfpW, "h", mfpH),
        "landscape_panel", landscapeRect,
        "portrait_panel", portraitRect,
        "primary_panel", primaryRect,
        "portrait_prev", Map("x", portraitRect["x"] + 6, "y", portraitRect["y"] + Floor((portraitRect["h"] - 22) / 2), "w", 18, "h", 22),
        "portrait_next", Map("x", portraitRect["x"] + portraitRect["w"] - 24, "y", portraitRect["y"] + Floor((portraitRect["h"] - 22) / 2), "w", 18, "h", 22),
        "portrait_trash", Map("x", portraitRect["x"] + Floor((portraitRect["w"] - 30) / 2), "y", portraitRect["y"] + Floor((portraitRect["h"] - 36) / 2), "w", 30, "h", 16),
        "portrait_lock", Map("x", portraitRect["x"] + Floor((portraitRect["w"] - 30) / 2), "y", portraitRect["y"] + Floor((portraitRect["h"] - 36) / 2) + 20, "w", 30, "h", 16),
        "primary_prev", Map("x", primaryRect["x"] + 6, "y", primaryRect["y"] + Floor((primaryRect["h"] - 22) / 2), "w", 18, "h", 22),
        "primary_next", Map("x", primaryRect["x"] + primaryRect["w"] - 24, "y", primaryRect["y"] + Floor((primaryRect["h"] - 22) / 2), "w", 18, "h", 22),
        "quarter_button", Map("x", primaryRect["x"] + Floor((primaryRect["w"] - 28) / 2), "y", primaryRect["y"] + Floor((primaryRect["h"] - 16) / 2), "w", 28, "h", 16),
        "landscape_prev", Map("x", landscapeRect["x"] + 6, "y", landscapeRect["y"] + Floor((landscapeRect["h"] - 22) / 2), "w", 18, "h", 22),
        "landscape_next", Map("x", landscapeRect["x"] + landscapeRect["w"] - 24, "y", landscapeRect["y"] + Floor((landscapeRect["h"] - 22) / 2), "w", 18, "h", 22),
        "landscape_trash", Map("x", landscapeRect["x"] + Floor((landscapeRect["w"] - 30) / 2), "y", landscapeRect["y"] + Floor((landscapeRect["h"] - 36) / 2), "w", 30, "h", 16),
        "landscape_lock", Map("x", landscapeRect["x"] + Floor((landscapeRect["w"] - 30) / 2), "y", landscapeRect["y"] + Floor((landscapeRect["h"] - 36) / 2) + 20, "w", 30, "h", 16),
        "link_toggle", Map("x", linkX, "y", linkY, "w", linkW, "h", 18),
        "osr2_panel", Map("x", osr2X, "y", osr2Y, "w", osr2W, "h", osr2H),
        "broker_panel", Map("x", statusRowX, "y", statusRowY, "w", statusChipSize, "h", statusChipSize),
        "controller_panel", Map("x", statusRowX + statusChipSize + statusChipGap, "y", statusRowY, "w", statusChipSize, "h", statusChipSize),
        "fmode_panel", Map("x", statusRowX + (statusChipSize + statusChipGap) * 2, "y", statusRowY, "w", statusChipSize, "h", statusChipSize)
    )
}

ApplyFunTimeDashboardLayout() {
    global funTimeDashboardControls

    if (!IsObject(funTimeDashboardControls))
        return

    layout := ""
    GetDashboardMonitorPreviewLayout(&layout)

    SetDashboardControlRect(funTimeDashboardControls["title"], layout["title"]["x"], layout["title"]["y"], layout["title"]["w"], layout["title"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["main_monitor"], layout["main_monitor"]["x"], layout["main_monitor"]["y"], layout["main_monitor"]["w"], layout["main_monitor"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["secondary_monitor"], layout["secondary_monitor"]["x"], layout["secondary_monitor"]["y"], layout["secondary_monitor"]["w"], layout["secondary_monitor"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["main_status_strip"], layout["main_status_strip"]["x"], layout["main_status_strip"]["y"], layout["main_status_strip"]["w"], layout["main_status_strip"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["mfp_panel"], layout["mfp_panel"]["x"], layout["mfp_panel"]["y"], layout["mfp_panel"]["w"], layout["mfp_panel"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["landscape_panel"], layout["landscape_panel"]["x"], layout["landscape_panel"]["y"], layout["landscape_panel"]["w"], layout["landscape_panel"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["portrait_panel"], layout["portrait_panel"]["x"], layout["portrait_panel"]["y"], layout["portrait_panel"]["w"], layout["portrait_panel"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["primary_panel"], layout["primary_panel"]["x"], layout["primary_panel"]["y"], layout["primary_panel"]["w"], layout["primary_panel"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["portrait_prev"], layout["portrait_prev"]["x"], layout["portrait_prev"]["y"], layout["portrait_prev"]["w"], layout["portrait_prev"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["portrait_next"], layout["portrait_next"]["x"], layout["portrait_next"]["y"], layout["portrait_next"]["w"], layout["portrait_next"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["portrait_lock"], layout["portrait_lock"]["x"], layout["portrait_lock"]["y"], layout["portrait_lock"]["w"], layout["portrait_lock"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["portrait_trash"], layout["portrait_trash"]["x"], layout["portrait_trash"]["y"], layout["portrait_trash"]["w"], layout["portrait_trash"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["primary_prev"], layout["primary_prev"]["x"], layout["primary_prev"]["y"], layout["primary_prev"]["w"], layout["primary_prev"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["primary_next"], layout["primary_next"]["x"], layout["primary_next"]["y"], layout["primary_next"]["w"], layout["primary_next"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["quarter_button"], layout["quarter_button"]["x"], layout["quarter_button"]["y"], layout["quarter_button"]["w"], layout["quarter_button"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["landscape_prev"], layout["landscape_prev"]["x"], layout["landscape_prev"]["y"], layout["landscape_prev"]["w"], layout["landscape_prev"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["landscape_next"], layout["landscape_next"]["x"], layout["landscape_next"]["y"], layout["landscape_next"]["w"], layout["landscape_next"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["landscape_lock"], layout["landscape_lock"]["x"], layout["landscape_lock"]["y"], layout["landscape_lock"]["w"], layout["landscape_lock"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["landscape_trash"], layout["landscape_trash"]["x"], layout["landscape_trash"]["y"], layout["landscape_trash"]["w"], layout["landscape_trash"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["link_toggle"], layout["link_toggle"]["x"], layout["link_toggle"]["y"], layout["link_toggle"]["w"], layout["link_toggle"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["osr2_panel"], layout["osr2_panel"]["x"], layout["osr2_panel"]["y"], layout["osr2_panel"]["w"], layout["osr2_panel"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["broker_panel"], layout["broker_panel"]["x"], layout["broker_panel"]["y"], layout["broker_panel"]["w"], layout["broker_panel"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["controller_panel"], layout["controller_panel"]["x"], layout["controller_panel"]["y"], layout["controller_panel"]["w"], layout["controller_panel"]["h"])
    SetDashboardControlRect(funTimeDashboardControls["fmode_panel"], layout["fmode_panel"]["x"], layout["fmode_panel"]["y"], layout["fmode_panel"]["w"], layout["fmode_panel"]["h"])
}

ClipLabelFromPath(path) {
    if (path = "")
        return "(none)"
    SplitPath(path, &name)
    return name
}

PanelLabelText(label) {
    global LABEL_PORTRAIT_VLC, LABEL_LANDSCAPE_VLC, LABEL_PRIMARY_VLC, LABEL_PRIMARY_ROBOT
    switch label {
        case LABEL_PORTRAIT_VLC:
            return "Portrait AI`nVLC"
        case LABEL_LANDSCAPE_VLC:
            return "Landscape AI`nVLC"
        case LABEL_PRIMARY_VLC:
            return "Non-AI`nVLC"
        case LABEL_PRIMARY_ROBOT:
            return "Non-AI`nRobot Hand"
        default:
            return label
    }
}

PrimaryPanelShouldHighlight() {
    global fModeEnabled, PRIMARY_VLC_PORT
    if (fModeEnabled)
        return true
    path := GetCurrentFilePath(PRIMARY_VLC_PORT)
    return path != "" && HasMatchingFunscript(path)
}

SatellitePanelShouldHighlight(port) {
    global fModeEnabled
    if (fModeEnabled)
        return true
    return IsFavoritePath(GetCurrentFilePath(port), ReadFavsContent())
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

CreateFunTimeDashboard() {
    global funTimeDashboardGui, funTimeDashboardControls
    global dashboardLastX, dashboardLastY, dashboardLastW, dashboardLastH
    global COLOR_BG, COLOR_PANEL, COLOR_TEXT, COLOR_MUTED

    guiObj := Gui("+AlwaysOnTop -Caption +ToolWindow", "Fun Time Dashboard")
    guiObj.BackColor := COLOR_BG
    guiObj.SetFont("s9 Bold", "Segoe UI")
    controls := Map()

    AddDashboardText(guiObj, controls, "title", "x16 y12 w300 h20 BackgroundTrans c" . COLOR_TEXT, "Fun Time")
    AddDashboardText(guiObj, controls, "main_monitor", "x16 y40 w146 h194 Border Background" . COLOR_PANEL . " c" . COLOR_MUTED, "")
    AddDashboardText(guiObj, controls, "secondary_monitor", "x176 y40 w146 h194 Border Background" . COLOR_PANEL . " c" . COLOR_MUTED, "")
    AddDashboardText(guiObj, controls, "main_status_strip", "x20 y20 w80 h30 Border Background" . COLOR_PANEL . " c" . COLOR_MUTED, "")

    AddDashboardText(guiObj, controls, "portrait_panel", "x46 y66 w86 h48 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, LABEL_PORTRAIT_VLC)
    AddDashboardText(guiObj, controls, "portrait_prev", "x24 y74 w18 h24 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "<", (*) => (CancelLock(2), VlcHttpCmd(VLC2_PORT, "pl_previous")))
    AddDashboardText(guiObj, controls, "portrait_next", "x136 y74 w18 h24 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, ">", (*) => (CancelLock(2), VlcHttpCmd(VLC2_PORT, "pl_next")))
    AddDashboardText(guiObj, controls, "portrait_lock", "x84 y118 w34 h18 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "Lock", (*) => ToggleLock(2))
    AddDashboardText(guiObj, controls, "portrait_trash", "x120 y118 w34 h18 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "Trash", (*) => Discard(2))

    AddDashboardText(guiObj, controls, "primary_panel", "x46 y150 w86 h62 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, LABEL_PRIMARY_VLC)
    AddDashboardText(guiObj, controls, "primary_prev", "x24 y166 w18 h32 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "<", (*) => HandlePrevAction())
    AddDashboardText(guiObj, controls, "primary_next", "x136 y166 w18 h32 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, ">", (*) => HandleNextAction())
    AddDashboardText(guiObj, controls, "quarter_button", "x104 y216 w50 h18 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "1/4", (*) => QueueRobotHandOffsetQuarterCycle())

    AddDashboardText(guiObj, controls, "mfp_panel", "x188 y82 w56 h76 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, LABEL_MFP)
    AddDashboardText(guiObj, controls, "landscape_panel", "x270 y66 w42 h148 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, LABEL_LANDSCAPE_VLC)
    AddDashboardText(guiObj, controls, "landscape_prev", "x248 y118 w18 h24 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "<", (*) => (CancelLock(3), VlcHttpCmd(VLC3_PORT, "pl_previous")))
    AddDashboardText(guiObj, controls, "landscape_next", "x316 y118 w18 h24 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, ">", (*) => (CancelLock(3), VlcHttpCmd(VLC3_PORT, "pl_next")))
    AddDashboardText(guiObj, controls, "landscape_lock", "x242 y188 w42 h18 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "Lock", (*) => ToggleLock(3))
    AddDashboardText(guiObj, controls, "landscape_trash", "x286 y188 w48 h18 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "Trash", (*) => Discard(3))

    AddDashboardText(guiObj, controls, "link_toggle", "x132 y236 w74 h18 Border Center Background" . COLOR_LINK_ON . " c" . COLOR_TEXT, "Robot Link", (*) => ToggleRobotHandEnabled())
    AddDashboardText(guiObj, controls, "osr2_panel", "x82 y260 w176 h44 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, LABEL_OSR2)
    guiObj.SetFont("s7 Bold", "Segoe UI")
    AddDashboardText(guiObj, controls, "broker_panel", "x16 y314 w16 h16 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "b")
    AddDashboardText(guiObj, controls, "controller_panel", "x120 y314 w16 h16 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "c")
    AddDashboardText(guiObj, controls, "fmode_panel", "x224 y314 w16 h16 Border Center Background" . COLOR_PANEL . " c" . COLOR_TEXT, "f")
    guiObj.SetFont("s9 Bold", "Segoe UI")

    funTimeDashboardGui := guiObj
    funTimeDashboardControls := controls
    ApplyFunTimeDashboardLayout()
    GetFunTimeDashboardRect(&x, &y, &w, &h)
    guiObj.Show("NA x" . x . " y" . y . " w" . w . " h" . h)
    dashboardLastX := x
    dashboardLastY := y
    dashboardLastW := w
    dashboardLastH := h
    UpdateFunTimeDashboard()
}

UpdateFunTimeDashboard() {
    global funTimeDashboardGui, funTimeDashboardControls
    global dashboardLastX, dashboardLastY, dashboardLastW, dashboardLastH
    global robotHandMode, fModeEnabled, locked2, locked3
    global PRIMARY_VLC_PORT, VLC2_PORT, VLC3_PORT
    global COLOR_PANEL, COLOR_TEXT, COLOR_MUTED, COLOR_ACTIVE, COLOR_ACTIVE_ALT, COLOR_DISABLED, COLOR_WARNING, COLOR_LINK_ON, COLOR_LINK_OFF
    global LABEL_PRIMARY_VLC, LABEL_PRIMARY_ROBOT, LABEL_PORTRAIT_VLC, LABEL_LANDSCAPE_VLC, LABEL_OSR2, LABEL_MFP, LABEL_BROKER, LABEL_CONTROLLER, LABEL_F_MODE

    if (!IsObject(funTimeDashboardGui) || !IsObject(funTimeDashboardControls))
        return

    ApplyFunTimeDashboardLayout()

    primaryPath := GetCurrentFilePath(PRIMARY_VLC_PORT)
    portraitPath := GetCurrentFilePath(VLC2_PORT)
    landscapePath := GetCurrentFilePath(VLC3_PORT)
    osr2Auto := RobotHandModeState() = "1"
    robotHandEnabledNow := RobotHandEnabled()
    GetDashboardStatusSnapshot(&brokerRunningNow, &mfpConnectedNow)
    primaryUsesRobotHand := robotHandMode && robotHandEnabledNow
    primaryColor := PrimaryPanelShouldHighlight() ? COLOR_ACTIVE_ALT : COLOR_PANEL
    portraitColor := SatellitePanelShouldHighlight(VLC2_PORT) ? COLOR_ACTIVE_ALT : COLOR_PANEL
    landscapeColor := SatellitePanelShouldHighlight(VLC3_PORT) ? COLOR_ACTIVE_ALT : COLOR_PANEL
    osr2Color := osr2Auto ? COLOR_ACTIVE : COLOR_WARNING
    mfpColor := mfpConnectedNow ? COLOR_ACTIVE : COLOR_DISABLED
    brokerColor := brokerRunningNow ? COLOR_ACTIVE : COLOR_DISABLED
    controllerColor := COLOR_ACTIVE
    fModeColor := fModeEnabled ? COLOR_ACTIVE_ALT : COLOR_PANEL

    if (primaryUsesRobotHand)
        primaryColor := osr2Color

    SetDashboardControlVisual(funTimeDashboardControls["portrait_panel"], PanelLabelText(LABEL_PORTRAIT_VLC) . "`n" . ClipLabelFromPath(portraitPath), portraitColor)
    SetDashboardControlVisual(funTimeDashboardControls["landscape_panel"], PanelLabelText(LABEL_LANDSCAPE_VLC) . "`n" . ClipLabelFromPath(landscapePath), landscapeColor)
    SetDashboardControlVisual(funTimeDashboardControls["primary_panel"], PanelLabelText(primaryUsesRobotHand ? LABEL_PRIMARY_ROBOT : LABEL_PRIMARY_VLC) . "`n" . ClipLabelFromPath(primaryUsesRobotHand ? "" : primaryPath), primaryColor)
    SetDashboardControlVisual(funTimeDashboardControls["osr2_panel"], LABEL_OSR2 . "`n" . (osr2Auto ? "auto" : "controlled"), osr2Color)
    SetDashboardControlVisual(funTimeDashboardControls["mfp_panel"], LABEL_MFP . "`n" . (mfpConnectedNow ? "connected" : "disconnected"), mfpColor)
    SetDashboardControlVisual(funTimeDashboardControls["broker_panel"], "b", brokerColor)
    SetDashboardControlVisual(funTimeDashboardControls["controller_panel"], "c", controllerColor)
    SetDashboardControlVisual(funTimeDashboardControls["fmode_panel"], "f", fModeColor)
    SetDashboardControlVisual(funTimeDashboardControls["portrait_lock"], "Lock", locked2 ? COLOR_ACTIVE : COLOR_PANEL)
    SetDashboardControlVisual(funTimeDashboardControls["landscape_lock"], "Lock", locked3 ? COLOR_ACTIVE : COLOR_PANEL)
    SetDashboardControlVisual(funTimeDashboardControls["portrait_trash"], "Trash", COLOR_WARNING)
    SetDashboardControlVisual(funTimeDashboardControls["landscape_trash"], "Trash", COLOR_WARNING)
    SetDashboardControlVisual(funTimeDashboardControls["link_toggle"], robotHandEnabledNow ? "Robot Link" : "Broken Link", robotHandEnabledNow ? COLOR_LINK_ON : COLOR_LINK_OFF)
    SetDashboardControlVisual(funTimeDashboardControls["quarter_button"], "1/4", primaryUsesRobotHand ? osr2Color : COLOR_PANEL)
    WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, brokerRunningNow, mfpConnectedNow)

    GetFunTimeDashboardRect(&x, &y, &w, &h)
    if (x != dashboardLastX || y != dashboardLastY || w != dashboardLastW || h != dashboardLastH) {
        try WinMove(x, y, w, h, "ahk_id " funTimeDashboardGui.Hwnd)
        dashboardLastX := x
        dashboardLastY := y
        dashboardLastW := w
        dashboardLastH := h
    }
}

WriteDashboardStateSnapshot(primaryPath, portraitPath, landscapePath, primaryUsesRobotHand, osr2Auto, robotHandEnabled, brokerRunning, mfpConnected) {
    global DASHBOARD_STATE_FILE, LABEL_PRIMARY_ROBOT, LABEL_PRIMARY_VLC, LABEL_PORTRAIT_VLC, LABEL_LANDSCAPE_VLC
    global fModeEnabled

    IniWrite(brokerRunning ? "1" : "0", DASHBOARD_STATE_FILE, "broker", "running")
    IniWrite("1", DASHBOARD_STATE_FILE, "controller", "running")
    IniWrite(fModeEnabled ? "1" : "0", DASHBOARD_STATE_FILE, "fmode", "enabled")
    IniWrite(robotHandEnabled ? "1" : "0", DASHBOARD_STATE_FILE, "robot_link", "enabled")
    IniWrite(osr2Auto ? "auto" : "controlled", DASHBOARD_STATE_FILE, "osr2", "mode")
    IniWrite(mfpConnected ? "1" : "0", DASHBOARD_STATE_FILE, "mfp", "connected")

    IniWrite(primaryUsesRobotHand ? LABEL_PRIMARY_ROBOT : LABEL_PRIMARY_VLC, DASHBOARD_STATE_FILE, "primary", "label")
    IniWrite(ClipLabelFromPath(primaryUsesRobotHand ? "" : primaryPath), DASHBOARD_STATE_FILE, "primary", "clip")
    IniWrite(PrimaryPanelShouldHighlight() ? "1" : "0", DASHBOARD_STATE_FILE, "primary", "highlight")
    IniWrite(primaryUsesRobotHand ? "osr2" : "", DASHBOARD_STATE_FILE, "primary", "accent")

    IniWrite(LABEL_PORTRAIT_VLC, DASHBOARD_STATE_FILE, "portrait", "label")
    IniWrite(ClipLabelFromPath(portraitPath), DASHBOARD_STATE_FILE, "portrait", "clip")
    IniWrite(SatellitePanelShouldHighlight(VLC2_PORT) ? "1" : "0", DASHBOARD_STATE_FILE, "portrait", "highlight")
    IniWrite("", DASHBOARD_STATE_FILE, "portrait", "accent")

    IniWrite(LABEL_LANDSCAPE_VLC, DASHBOARD_STATE_FILE, "landscape", "label")
    IniWrite(ClipLabelFromPath(landscapePath), DASHBOARD_STATE_FILE, "landscape", "clip")
    IniWrite(SatellitePanelShouldHighlight(VLC3_PORT) ? "1" : "0", DASHBOARD_STATE_FILE, "landscape", "highlight")
    IniWrite("", DASHBOARD_STATE_FILE, "landscape", "accent")
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

IsSupportedVideoPath(path) {
    SplitPath(path, , , &ext)
    ext := "." . StrLower(ext)
    return ext = ".mp4" || ext = ".mkv" || ext = ".mov" || ext = ".avi" || ext = ".webm" || ext = ".m4v"
}

NormalizePathKey(path) {
    return StrLower(Trim(path))
}

BuildMirroredFunscriptPath(videoPath) {
    global PRIMARY_VLC_SOURCES

    for sourcePart in StrSplit(PRIMARY_VLC_SOURCES, "|") {
        sourceRoot := Trim(sourcePart)
        if (sourceRoot = "" || !DirExist(sourceRoot))
            continue

        sourceRootNorm := RTrim(sourceRoot, "\/")
        prefix := sourceRootNorm . "\"
        if (SubStr(videoPath, 1, StrLen(prefix)) != prefix)
            continue

        relativePath := SubStr(videoPath, StrLen(prefix) + 1)
        funscriptRoot := StrReplace(sourceRootNorm, "\videos\videos\", "\videos\scripts\scripts\")
        return funscriptRoot . "\" . RegExReplace(relativePath, "\.[^.\\\/]+$", ".funscript")
    }

    return ""
}

HasMatchingFunscript(videoPath) {
    funscriptPath := BuildMirroredFunscriptPath(videoPath)
    return funscriptPath != "" && FileExist(funscriptPath)
}

ReadFavsContent() {
    global FAVS_FILE
    if !FileExist(FAVS_FILE)
        return ""
    try return FileRead(FAVS_FILE, "UTF-8")
    catch {
        return ""
    }
}

IsFavoritePath(videoPath, favsContent) {
    if (videoPath = "" || favsContent = "")
        return false
    return InStr(favsContent, videoPath, false) > 0
}

UrlEncodeQueryValue(text) {
    out := ""
    Loop Parse, text {
        ch := A_LoopField
        code := Ord(ch)
        if ((code >= 0x30 && code <= 0x39)
            || (code >= 0x41 && code <= 0x5A)
            || (code >= 0x61 && code <= 0x7A)
            || InStr("-_.~", ch)) {
            out .= ch
            continue
        }

        byteCount := StrPut(ch, "UTF-8") - 1
        bytes := Buffer(byteCount, 0)
        StrPut(ch, bytes, "UTF-8")
        Loop byteCount {
            out .= "%" . Format("{:02X}", NumGet(bytes, A_Index - 1, "UChar"))
        }
    }
    return out
}

SendVlcInputCommand(port, command, fullPath) {
    uri := ToFileUri(fullPath)
    if (uri = "")
        return false
    VlcHttpReq(port, "/requests/status.xml?command=" . command . "&input=" . UrlEncodeQueryValue(uri), &st)
    return st = 200
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
    if !FileExist(playlistPath)
        return false

    VlcHttpCmd(port, "pl_empty")
    VlcHttpCmd(port, "pl_stop")
    Sleep 180

    if !SendVlcInputCommand(port, "in_play", playlistPath)
        return false

    if (repeatMode != "")
        SetRepeatMode(port, repeatMode)
    return true
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

PrepareChromeOverlayManifest()

PositionAll(pid1, pid2, pid3, pidM)
SetTopMost(pid1, pid2, pid3, pidM)
MaybeLaunchChromeOverlay(pidM)
CreateFunTimeDashboard()
SetTimer(UpdateFunTimeDashboard, 500)

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
    global isShuttingDown, pid1, pid2, pid3, pidM, pidR, pidA, funTimeDashboardGui
    if (isShuttingDown)
        return
    isShuttingDown := true
    Log("Shutdown requested")
    SetTimer(SyncRobotHandState, 0)
    SetTimer(UpdateFunTimeDashboard, 0)
    try {
        if (IsObject(funTimeDashboardGui))
            funTimeDashboardGui.Destroy()
    }

    for pid in [pid1, pid2, pid3, pidM, pidR, pidA]
        TryClosePid(pid)

    Sleep 700

    for pid in [pid1, pid2, pid3, pidM, pidR, pidA]
        TryKillPid(pid)

    Sleep 300

    for pid in [pid1, pid2, pid3, pidM, pidR, pidA]
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
    global PRIMARY_TOP_RATIO, LANDSCAPE_WIDTH_RATIO, MFP_WIDTH_RATIO, MFP_HEIGHT_RATIO
    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)
    mainL := mainRect["x"], mainT := mainRect["y"], mainW := mainRect["w"], mainH := mainRect["h"]
    secondaryL := secondaryRect["x"], secondaryT := secondaryRect["y"], secondaryW := secondaryRect["w"], secondaryH := secondaryRect["h"]

    portraitH := Floor(secondaryH * Clamp01(PRIMARY_TOP_RATIO))
    primaryH := secondaryH - portraitH

    MovePidWindow(pid2, secondaryL, secondaryT, secondaryW, portraitH)
    MovePidWindow(pid1, secondaryL, secondaryT + portraitH, secondaryW, primaryH)
    landscapeW := Floor(mainW * Clamp01(LANDSCAPE_WIDTH_RATIO))
    landscapeX := mainL + (mainW - landscapeW)
    MovePidWindow(pid3, landscapeX, mainT, landscapeW, mainH)

    PositionMfpWindow(pidM)
}

PositionMfpWindow(pidM) {
    layout := ""
    GetDashboardMonitorPreviewLayout(&layout)
    hwnd := WinWait("ahk_pid " pidM, , 10)

    GetActualMfpSize(&moveW, &moveH)
    stack := ""
    GetLeftPartitionStackLayout(layout["dashboard_w"], layout["dashboard_h"], moveW, moveH, &stack)
    moveX := stack["mfp_x"]
    moveY := stack["mfp_y"]

    Loop 3 {
        WinRestore("ahk_id " hwnd)
        WinMove(moveX, moveY, moveW, moveH, "ahk_id " hwnd)
        Sleep 80
        WinGetPos(&actualX, &actualY, &actualW, &actualH, "ahk_id " hwnd)
        GetLeftPartitionStackLayout(layout["dashboard_w"], layout["dashboard_h"], actualW, actualH, &stack)
        deltaX := stack["mfp_x"] - actualX
        deltaY := stack["mfp_y"] - actualY
        if (Abs(deltaX) <= 1 && Abs(deltaY) <= 1)
            break
        moveX += deltaX
        moveY += deltaY
        moveW := actualW
        moveH := actualH
    }
}

GetMfpRect(&x, &y, &w, &h) {
    layout := ""
    GetDashboardMonitorPreviewLayout(&layout)
    GetActualMfpSize(&mfpW, &mfpH)
    stack := ""
    GetLeftPartitionStackLayout(layout["dashboard_w"], layout["dashboard_h"], mfpW, mfpH, &stack)
    x := stack["mfp_x"]
    y := stack["mfp_y"]
    w := stack["mfp_w"]
    h := stack["mfp_h"]
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

GetLeftPartitionStackLayout(dashboardW, dashboardH, mfpW, mfpH, &stack) {
    global LANDSCAPE_WIDTH_RATIO
    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)
    mainL := mainRect["x"], mainT := mainRect["y"], mainW := mainRect["w"], mainH := mainRect["h"]
    landscapeW := Floor(mainW * Clamp01(LANDSCAPE_WIDTH_RATIO))
    leftW := mainW - landscapeW
    dashboardX := mainL + Floor((leftW - dashboardW) / 2)
    mfpX := mainL + Floor((leftW - mfpW) / 2)
    gapY := Floor((mainH - dashboardH - mfpH) / 3)
    dashboardY := mainT + gapY
    mfpY := dashboardY + dashboardH + gapY
    stack := Map(
        "dashboard_x", dashboardX,
        "dashboard_y", dashboardY,
        "mfp_x", mfpX,
        "mfp_y", mfpY,
        "mfp_w", mfpW,
        "mfp_h", mfpH
    )
}

GetChromeOverlayRect(&x, &y, &w, &h) {
    global LANDSCAPE_WIDTH_RATIO
    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)
    mainL := mainRect["x"], mainT := mainRect["y"], mainW := mainRect["w"], mainH := mainRect["h"]
    landscapeW := Floor(mainW * Clamp01(LANDSCAPE_WIDTH_RATIO))
    w := mainW - landscapeW
    h := mainH
    x := mainL
    y := mainT
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

PrepareChromeOverlayManifest() {
    global ROBOT_HAND_PY, CHROME_MANIFEST_FILE, CONFIG_PATH
    try FileDelete(CHROME_MANIFEST_FILE)
    cmd := Q(ROBOT_HAND_PY)
        . " -m fun_time.chrome_overlay"
        . " --config " . Q(CONFIG_PATH)
        . " --output " . Q(CHROME_MANIFEST_FILE)
    try RunWait(cmd, PROJECT_DIR, "Hide")
}

MaybeLaunchChromeOverlay(pidM) {
    global CHROME_MANIFEST_FILE, CHROME_SHORTCUT_PATH

    manifest := ReadChromeOverlayManifest(CHROME_MANIFEST_FILE)
    if (manifest.profileDir = "" || manifest.urls.Length = 0)
        return

    existing := GetVisibleChromeWindowHandles()
    launchSpec := BuildChromeLaunchSpec(manifest)
    if (launchSpec.cmd = "") {
        Log("Chrome overlay skipped because the Chrome shortcut could not be resolved")
        return
    }
    try Run(launchSpec.cmd, launchSpec.workDir, , &chromePid)

    newHwnd := WaitForNewChromeWindow(existing, 8000)
    if (!newHwnd) {
        Log("Chrome overlay skipped because the Chrome launch command did not produce a new visible window")
        return
    }

    GetChromeOverlayRect(&x, &y, &w, &h)
    try {
        WinRestore("ahk_id " newHwnd)
        WinMove(x, y, w, h, "ahk_id " newHwnd)
        WinSetAlwaysOnTop(false, "ahk_id " newHwnd)
    }
    try {
        WinSetAlwaysOnTop(true, "ahk_pid " pidM)
        WinActivate("ahk_pid " pidM)
    }
    Log("Chrome overlay positioned using direct launch for profile " . manifest.profileDir)
}

BuildChromeLaunchSpec(manifest) {
    global CHROME_SHORTCUT_PATH

    target := "", workDir := "", args := "", description := "", iconPath := "", iconNum := 0, runState := 0
    try FileGetShortcut(CHROME_SHORTCUT_PATH, &target, &workDir, &args, &description, &iconPath, &iconNum, &runState)
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

ReadChromeOverlayManifest(path) {
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
    t0 := A_TickCount
    loop {
        txt := VlcHttpReq(port, "/requests/status.xml", &st)
        if (st = 200 && txt != "" && InStr(txt, "<state>"))
            return true
        if (A_TickCount - t0 > timeoutMs)
            break
        Sleep 200
    }
    Log("VLC HTTP interface failed to come up on port " . port)
    MsgBox("VLC HTTP interface did not come up on port " . port . "`nControls for that player will not work until this is resolved.", "fun_time", "Icon!")
    return false
}

VlcHttpCmd(port, cmd) {
    VlcHttpReq(port, "/requests/status.xml?command=" . cmd, &st)
}

GetLoopRepeat(port, &loopVal, &repeatVal) {
    loopVal := "", repeatVal := ""
    xml := VlcHttpReq(port, "/requests/status.xml", &st)
    if (st != 200 || xml = "")
        return false
    if RegExMatch(xml, "<loop>([^<]+)</loop>", &m1)
        loopVal := m1[1]
    if RegExMatch(xml, "<repeat>([^<]+)</repeat>", &m2)
        repeatVal := m2[1]
    return true
}

ToBool(v) {
    v := StrLower(Trim(v))
    return (v = "1" || v = "true" || v = "yes")
}

GetRepeatMode(port, &mode) {
    if !GetLoopRepeat(port, &lv, &rv)
        return false

    if (rv != "" && ToBool(rv)) {
        mode := "one"
    } else if (lv != "" && ToBool(lv)) {
        mode := "all"
    } else {
        mode := "off"
    }
    return true
}

GetVlcPlaybackState(port, &state) {
    state := ""
    xml := VlcHttpReq(port, "/requests/status.xml", &st)
    if (st != 200 || xml = "")
        return false
    if RegExMatch(xml, "<state>([^<]+)</state>", &m)
        state := StrLower(Trim(m[1]))
    return (state != "")
}

EnsurePrimaryVlcPlayback(shouldPlay) {
    global PRIMARY_VLC_PORT
    target := shouldPlay ? "playing" : "paused"
    loop 8 {
        if !GetVlcPlaybackState(PRIMARY_VLC_PORT, &state)
            break
        if (state = target)
            return true
        VlcHttpCmd(PRIMARY_VLC_PORT, "pl_pause")
        Sleep 120
    }
    Log("Primary VLC failed to reach playback state " . target)
    return false
}

SetRepeatMode(port, target) {
    loop 12 {
        if !GetRepeatMode(port, &m)
            return false
        if (m = target)
            return true

        if (target = "one")
            VlcHttpCmd(port, "pl_repeat")
        else
            VlcHttpCmd(port, "pl_loop")

        Sleep 120
    }
    return false
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
    xml := VlcHttpReq(port, "/requests/playlist_jstree.xml", &st)
    if (st != 200 || xml = "")
        return ""

    uri := ""
    if RegExMatch(xml, "i)uri=\x22([^\x22]+)\x22[^>]*current=\x22current\x22", &m)
        uri := m[1]
    else if RegExMatch(xml, "i)current=\x22current\x22[^>]*uri=\x22([^\x22]+)\x22", &m2)
        uri := m2[1]

    if (uri = "")
        return ""
    return DecodeFileUri(uri)
}

DecodeFileUri(uri) {
    if !InStr(uri, "file:///")
        return ""
    p := SubStr(uri, 9)
    p := UrlDecode(p)
    p := StrReplace(p, "/", "\")
    return p
}

UrlDecode(s) {
    s := StrReplace(s, "+", " ")
    while RegExMatch(s, "i)%([0-9A-F]{2})", &m) {
        s := StrReplace(s, m[0], Chr("0x" . m[1]))
    }
    return s
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
    global omniPaused, robotHandMode, pid1, pid2, pid3, pidM
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

    ; Remove always-on-top from all VLC windows and MFP so they stop blocking other windows.
    for pid in [pid1, pid2, pid3, pidM] {
        try WinSetAlwaysOnTop(false, "ahk_pid " pid)
    }

    Suspend true
}

LeaveOmniPause(skipPrimaryVlcPlaybackToggleOnResume := false) {
    global omniPaused, robotHandMode, pid1, pid2, pid3, pidM
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

    ; Restore always-on-top for the two secondary VLC windows and MFP.
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
