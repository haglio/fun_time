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
    global ROBOT_HAND_MODE_FILE
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

EnforceRobotHandWindowState(active, isTransition := false) {
    global pid1

    if (active) {
        if (isTransition) {
            try WinShow("Robot Hand")
            try WinSetAlwaysOnTop(false, "ahk_pid " pid1)
            try WinSetAlwaysOnTop(true, "Robot Hand")
            try WinActivate("Robot Hand")
        }
    } else {
        if (isTransition) {
            try WinHide("Robot Hand")
            try WinSetAlwaysOnTop(false, "Robot Hand")
            try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
        }
    }
}

UpdateFunTimeDashboard() {
    global DASHBOARD_ENABLED, pidM
    global DASHBOARD_STATE_FILE, fModeEnabled
    global robotHandMode, locked2, locked3
    if (!DASHBOARD_ENABLED)
        return

    osr2Auto := RobotHandModeState() = "1"
    robotHandEnabledNow := RobotHandEnabled()
    primaryUsesRobotHand := robotHandMode && robotHandEnabledNow
    mfpAlive := pidM && ProcessExist(pidM)
    args := "--output-file " . Q(DASHBOARD_STATE_FILE)
        . " --f-mode-enabled " . (fModeEnabled ? "1" : "0")
        . " --robot-link-enabled " . (robotHandEnabledNow ? "1" : "0")
        . " --osr2-mode " . (osr2Auto ? "auto" : "controlled")
        . " --mfp-alive " . (mfpAlive ? "1" : "0")
        . " --primary-uses-robot-hand " . (primaryUsesRobotHand ? "1" : "0")
        . " --portrait-locked " . (locked2 ? "1" : "0")
        . " --landscape-locked " . (locked3 ? "1" : "0")
    RunControllerDashboardBridgeAction(args)
}

EffectiveRobotHandModeState() {
    if (!RobotHandEnabled())
        return "0"
    return RobotHandModeState()
}

ApplyRobotHandPlanWindowState(plan) {
    if (plan["enforce_outputs"])
        EnforceRobotHandWindowState(plan["enforce_active"], plan["is_transition"])
}

SyncRobotHandState() {
    global robotHandMode, omniPaused
    global ROBOT_HAND_ENABLED_FILE, ROBOT_HAND_PAUSED_FILE, AUDIO_PAUSED_FILE
    global PRIMARY_VLC_PORT, VLC_PASS

    if (omniPaused)
        return

    planPath := BuildRobotHandPlanPath()
    extraArgs := "--enabled-file " . Q(ROBOT_HAND_ENABLED_FILE)
        . " --paused-file " . Q(ROBOT_HAND_PAUSED_FILE)
        . " --audio-paused-file " . Q(AUDIO_PAUSED_FILE)
        . " --primary-port " . PRIMARY_VLC_PORT
        . " --password " . Q(VLC_PASS)
    plan := RunControllerRobotHandAction("apply-sync-state", robotHandMode, RobotHandEnabled(), omniPaused, planPath, extraArgs)
    if !IsObject(plan)
        return
    robotHandMode := plan["next_robot_hand_mode"]
    if (plan["log_message"] != "")
        Log(plan["log_message"])
    ApplyRobotHandPlanWindowState(plan)
    UpdateFunTimeDashboard()
}

ToggleRobotHandEnabled() {
    global robotHandMode, omniPaused
    global ROBOT_HAND_ENABLED_FILE, ROBOT_HAND_PAUSED_FILE, AUDIO_PAUSED_FILE
    global PRIMARY_VLC_PORT, VLC_PASS
    planPath := BuildRobotHandPlanPath()
    extraArgs := "--enabled-file " . Q(ROBOT_HAND_ENABLED_FILE)
        . " --paused-file " . Q(ROBOT_HAND_PAUSED_FILE)
        . " --audio-paused-file " . Q(AUDIO_PAUSED_FILE)
        . " --primary-port " . PRIMARY_VLC_PORT
        . " --password " . Q(VLC_PASS)
    plan := RunControllerRobotHandAction("apply-toggle-enabled", robotHandMode, RobotHandEnabled(), omniPaused, planPath, extraArgs)
    if !IsObject(plan)
        return
    if (plan["log_message"] != "")
        Log(plan["log_message"])
    robotHandMode := plan["next_robot_hand_mode"]
    ApplyRobotHandPlanWindowState(plan)
    UpdateFunTimeDashboard()
}

ApplyFModePlaylists(enabled) {
    global PRIMARY_VLC_SOURCES, PORTRAIT_DIR, LANDSCAPE_DIR, FAVS_FILE, STATE_DIR
    global PRIMARY_VLC_PORT, VLC2_PORT, VLC3_PORT, VLC_PASS, locked2, locked3

    resultPath := BuildModesResultPath()
    try FileDelete(resultPath)
    args := "apply-fmode"
        . " --primary-sources " . Q(PRIMARY_VLC_SOURCES)
        . " --portrait-sources " . Q(PORTRAIT_DIR)
        . " --landscape-sources " . Q(LANDSCAPE_DIR)
        . " --favs-file " . Q(FAVS_FILE)
        . " --state-dir " . Q(STATE_DIR)
        . " --primary-port " . PRIMARY_VLC_PORT
        . " --portrait-port " . VLC2_PORT
        . " --landscape-port " . VLC3_PORT
        . " --password " . Q(VLC_PASS)
        . " --result-file " . Q(resultPath)
        . " --enabled " . (enabled ? "1" : "0")

    exitCode := RunControllerModesAction(args)
    if (exitCode = 3) {
        Log("F-mode toggle aborted because one or more playlists would be empty")
        return false
    }
    if (exitCode != 0)
        return false
    result := LoadModesActionResult(resultPath)
    if !IsObject(result)
        return false
    locked2 := result["next_locked2"]
    locked3 := result["next_locked3"]
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

ShutdownAll() {
    global isShuttingDown, pid1, pid2, pid3, pidM, pidD, pidR, pidA
    if (isShuttingDown)
        return
    isShuttingDown := true
    Log("Shutdown requested")
    SetTimer(SyncRobotHandState, 0)
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
    args := "restart-broker --project-dir " . Q(PROJECT_DIR)
    RunControllerStartupAction(args)
}

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
    global VLC2_PORT, VLC3_PORT, PRIMARY_VLC_PORT, VLC_PASS
    global ROBOT_HAND_PAUSED_FILE, AUDIO_PAUSED_FILE
    planPath := BuildOmniPausePlanPath()
    extraArgs := "--portrait-port " . VLC2_PORT
        . " --landscape-port " . VLC3_PORT
        . " --primary-port " . PRIMARY_VLC_PORT
        . " --password " . Q(VLC_PASS)
        . " --robot-hand-paused-file " . Q(ROBOT_HAND_PAUSED_FILE)
        . " --audio-paused-file " . Q(AUDIO_PAUSED_FILE)
    plan := RunControllerOmniPauseAction("apply-enter", omniPaused, robotHandMode, false, planPath, extraArgs)
    if !IsObject(plan)
        return

    omniPaused := plan["next_omni_paused"]
    Log(plan["log_message"])

    if (plan["robot_hand_branch"]) {
        try WinSetAlwaysOnTop(false, "Robot Hand")
    }

    for pid in [pid1, pid2, pid3, pidM, pidD] {
        try WinSetAlwaysOnTop(false, "ahk_pid " pid)
    }

    Suspend true
}

LeaveOmniPause(skipPrimaryVlcPlaybackToggleOnResume := false) {
    global omniPaused, robotHandMode, pid1, pid2, pid3, pidM, pidD
    global VLC2_PORT, VLC3_PORT, PRIMARY_VLC_PORT, VLC_PASS
    global ROBOT_HAND_PAUSED_FILE, AUDIO_PAUSED_FILE
    planPath := BuildOmniPausePlanPath()
    extraArgs := "--portrait-port " . VLC2_PORT
        . " --landscape-port " . VLC3_PORT
        . " --primary-port " . PRIMARY_VLC_PORT
        . " --password " . Q(VLC_PASS)
        . " --robot-hand-paused-file " . Q(ROBOT_HAND_PAUSED_FILE)
        . " --audio-paused-file " . Q(AUDIO_PAUSED_FILE)
    plan := RunControllerOmniPauseAction("apply-leave", omniPaused, robotHandMode, skipPrimaryVlcPlaybackToggleOnResume, planPath, extraArgs)
    if !IsObject(plan)
        return

    Log(plan["log_message"])
    Suspend false

    if (!plan["robot_hand_branch"]) {
        try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
    }

    try WinSetAlwaysOnTop(true, "ahk_pid " pidD)
    try WinSetAlwaysOnTop(true, "ahk_pid " pid2)
    try WinSetAlwaysOnTop(true, "ahk_pid " pid3)
    try WinSetAlwaysOnTop(true, "ahk_pid " pidM)

    omniPaused := plan["next_omni_paused"]
    SyncRobotHandState()

    if (robotHandMode) {
        try WinSetAlwaysOnTop(true, "Robot Hand")
        try WinActivate("Robot Hand")
    }
}

StartController() {
    global ICON_PATH, DASHBOARD_ENABLED, DASHBOARD_CMD_FILE, DASHBOARD_STATE_FILE
    global pid1, pid2, pid3, pidM, pidD, pidR, pidA
    global MFP_EXE, PRIMARY_VLC_PORT, PRIMARY_VLC_SOURCES, VLC_PASS
    global VLC2_PORT, VLC3_PORT, PORTRAIT_DIR, LANDSCAPE_DIR
    global ROBOT_HAND_PY, ROBOT_HAND_MODULE, CONFIG_PATH, ROBOT_HAND_CLIPS
    global ROBOT_HAND_AUDIO_MODULE, ROBOT_HAND_AUDIO

    Log("Controller starting")
    if FileExist(ICON_PATH)
        TraySetIcon(ICON_PATH)

    OnExit(HandleControllerExit)

    args := "seed-robot-hand-state"
        . " --enabled-file " . Q(ROBOT_HAND_ENABLED_FILE)
        . " --paused-file " . Q(ROBOT_HAND_PAUSED_FILE)
        . " --audio-paused-file " . Q(AUDIO_PAUSED_FILE)
    RunControllerStartupAction(args)
    Log("Startup: reset Robot Hand enabled/paused state files")
    RestartBroker()
    Log("Startup: broker restart requested")

    coreResultPath := BuildStartupResultPath()
    args := "launch-core-apps"
        . " --project-dir " . Q(PROJECT_DIR)
        . " --vlc-exe " . Q(VLC_EXE)
        . " --mfp-exe " . Q(MFP_EXE)
        . " --primary-sources " . Q(PRIMARY_VLC_SOURCES)
        . " --portrait-sources " . Q(PORTRAIT_DIR)
        . " --landscape-sources " . Q(LANDSCAPE_DIR)
        . " --primary-port " . PRIMARY_VLC_PORT
        . " --portrait-port " . VLC2_PORT
        . " --landscape-port " . VLC3_PORT
        . " --password " . Q(VLC_PASS)
        . " --result-file " . Q(coreResultPath)
    if (RunControllerStartupAction(args) != 0)
        throw Error("Failed to launch core media stack")
    coreResult := LoadStartupActionResult(coreResultPath)
    if !IsObject(coreResult)
        throw Error("Failed to read core media stack startup result")
    pid1 := coreResult["primary_pid"]
    pidM := coreResult["mfp_pid"]
    pid2 := coreResult["portrait_pid"]
    pid3 := coreResult["landscape_pid"]
    Log("Startup: launched primary VLC pid=" . pid1)
    Log("Startup: launched MFP pid=" . pidM)
    Log("Startup: launched portrait VLC pid=" . pid2)
    Log("Startup: launched landscape VLC pid=" . pid3)
    Log("Startup: core VLC HTTP ready and initial playlists seeded")

    WinWait("ahk_pid " pidM, , 15)
    Sleep 5000
    Log("Startup: MFP window ready")

    PrepareRandomFavsBrowserManifest()
    Log("Startup: Random Favs Browser manifest prepared")

    PositionAll(pid1, pid2, pid3, pidM)
    SetTopMost(pid1, pid2, pid3, pidM)
    Log("Startup: core windows positioned and topmost set")
    MaybeLaunchRandomFavsBrowser(pidM)
    if (DASHBOARD_ENABLED) {
        try FileDelete(DASHBOARD_CMD_FILE)
        GetCurrentWindowLayout(&plan)
        pidD := LaunchDashboardApp(
            plan["dashboard"]["x"],
            plan["dashboard"]["y"],
            plan["dashboard"]["w"],
            plan["dashboard"]["h"],
            pidM
        )
        Log("Startup: launched dashboard pid=" . pidD)
        SetTimer(ProcessDashboardCommand, 150)
        UpdateFunTimeDashboard()
        Log("Startup: dashboard state seeded")
        Log("Startup: dashboard command timer running")
    } else {
        try FileDelete(DASHBOARD_CMD_FILE)
        try FileDelete(DASHBOARD_STATE_FILE)
        SetTimer(ProcessDashboardCommand, 150)
        Log("Startup: dashboard disabled")
    }

    Sleep 1200

    rx := 0, ry := 0, rw := 0, rh := 0
    GetRobotHandRect(&rx, &ry, &rw, &rh)
    Log("Startup: resolved Robot Hand rect x=" . rx . " y=" . ry . " w=" . rw . " h=" . rh)

    startupResultPath := BuildStartupResultPath()
    args := "launch-runtime-companions"
        . " --python-exe " . Q(ROBOT_HAND_PY)
        . " --robot-hand-module " . Q(ROBOT_HAND_MODULE)
        . " --audio-module " . Q(ROBOT_HAND_AUDIO_MODULE)
        . " --config " . Q(CONFIG_PATH)
        . " --clips-folder " . Q(ROBOT_HAND_CLIPS)
        . " --audio-folder " . Q(ROBOT_HAND_AUDIO)
        . " --x " . rx
        . " --y " . ry
        . " --width " . rw
        . " --height " . rh
        . " --result-file " . Q(startupResultPath)
    if (RunControllerStartupAction(args) != 0)
        throw Error("Failed to launch Robot Hand runtime companions")
    startupResult := LoadStartupActionResult(startupResultPath)
    if !IsObject(startupResult)
        throw Error("Failed to read Robot Hand runtime companion pids")
    pidR := startupResult["robot_hand_pid"]
    pidA := startupResult["audio_pid"]
    Log("Started Robot Hand listener pid=" . pidR)
    Log("Started Robot Hand audio pid=" . pidA)

    SetTimer(SyncRobotHandState, 200)
    Log("Startup: Robot Hand sync timer running")

    A_IconTip := "Fun Time Controller"
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Open Controller Log", ShowControllerLog)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Exit Fun Time", (*) => ShutdownAll())
    A_TrayMenu.AddStandard()
}
