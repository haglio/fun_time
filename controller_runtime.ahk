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

UpdateFunTimeDashboard() {
    global DASHBOARD_ENABLED, pidM
    global robotHandMode, locked2, locked3
    if (!DASHBOARD_ENABLED)
        return

    osr2Auto := RobotHandModeState() = "1"
    robotHandEnabledNow := RobotHandEnabled()
    primaryUsesRobotHand := robotHandMode && robotHandEnabledNow
    mfpAlive := pidM && ProcessExist(pidM)
    WriteDashboardStateSnapshot(primaryUsesRobotHand, osr2Auto, robotHandEnabledNow, mfpAlive, locked2, locked3)
}

WriteDashboardStateSnapshot(primaryUsesRobotHand, osr2Auto, robotHandEnabled, mfpAlive, portraitLocked, landscapeLocked) {
    global DASHBOARD_STATE_FILE
    global fModeEnabled
    static lastDashboardSnapshotText := ""

    snapshotText := "[fmode]`n"
        . "enabled=" . (fModeEnabled ? "1" : "0") . "`n"
        . "[robot_link]`n"
        . "enabled=" . (robotHandEnabled ? "1" : "0") . "`n"
        . "[osr2]`n"
        . "mode=" . (osr2Auto ? "auto" : "controlled") . "`n"
        . "[mfp]`n"
        . "alive=" . (mfpAlive ? "1" : "0") . "`n"
        . "[primary]`n"
        . "uses_robot_hand=" . (primaryUsesRobotHand ? "1" : "0") . "`n"
        . "locked=0`n"
        . "[portrait]`n"
        . "locked=" . (portraitLocked ? "1" : "0") . "`n"
        . "[landscape]`n"
        . "locked=" . (landscapeLocked ? "1" : "0") . "`n"

    if (snapshotText = lastDashboardSnapshotText)
        return
    lastDashboardSnapshotText := snapshotText
    FileDelete(DASHBOARD_STATE_FILE)
    FileAppend(snapshotText, DASHBOARD_STATE_FILE, "UTF-16")
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
    global robotHandMode, omniPaused

    if (omniPaused)
        return

    modeState := EffectiveRobotHandModeState()
    modeOn := (modeState = "1")

    if (modeOn && !robotHandMode) {
        robotHandMode := true
        Log("Entering Robot Hand mode")
        EnforceRobotHandOutputs(true, true)
        UpdateFunTimeDashboard()
    } else if (!modeOn && robotHandMode) {
        robotHandMode := false
        Log("Leaving Robot Hand mode")
        EnforceRobotHandOutputs(false, true)
        UpdateFunTimeDashboard()
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
    global VLC2_PORT, VLC3_PORT
    planPath := BuildOmniPausePlanPath()
    plan := RunControllerOmniPauseAction("enter", omniPaused, robotHandMode, false, planPath)
    if !IsObject(plan)
        return

    omniPaused := plan["next_omni_paused"]
    Log(plan["log_message"])

    if (plan["robot_hand_branch"]) {
        SendVlcCommand(VLC2_PORT, "pl_pause")
        SendVlcCommand(VLC3_PORT, "pl_pause")
        SetRobotHandPaused(true)
        SetRobotHandAudioPaused(true)
        try WinSetAlwaysOnTop(false, "Robot Hand")
    } else {
        EnsurePrimaryVlcPlayback(false)
        SendVlcCommand(VLC2_PORT, "pl_pause")
        SendVlcCommand(VLC3_PORT, "pl_pause")
    }

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
        SetRobotHandPaused(false)
        SetRobotHandAudioPaused(false)
        SendVlcCommand(VLC2_PORT, "pl_pause")
        SendVlcCommand(VLC3_PORT, "pl_pause")
    } else {
        if (plan["resume_primary_playback"])
            EnsurePrimaryVlcPlayback(true)
        SendVlcCommand(VLC2_PORT, "pl_pause")
        SendVlcCommand(VLC3_PORT, "pl_pause")
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

    SetRobotHandEnabled(true)
    SetRobotHandPaused(true)
    SetRobotHandAudioPaused(true)
    Log("Startup: reset Robot Hand enabled/paused state files")
    RestartBroker()
    Log("Startup: broker restart requested")

    pid1 := RunVLC(Join(
        "--no-one-instance --random --repeat",
        "--extraintf http",
        "--http-host 127.0.0.1",
        "--http-port " . PRIMARY_VLC_PORT,
        "--http-password " . Q(VLC_PASS)
    ), PRIMARY_VLC_SOURCES)
    Log("Startup: launched primary VLC pid=" . pid1)
    WaitForHttp(PRIMARY_VLC_PORT, 7000)
    Log("Startup: primary VLC HTTP ready")
    Sleep 300
    SendToPid(pid1, "n")
    Log("Startup: nudged primary VLC to next item")

    pidM := RunApp(MFP_EXE, "")
    Log("Startup: launched MFP pid=" . pidM)
    WinWait("ahk_pid " pidM, , 15)
    Sleep 5000
    Log("Startup: MFP window ready")

    pid2 := RunVLC(Join(
        "--no-one-instance --random --loop",
        "--extraintf http",
        "--http-host 127.0.0.1",
        "--http-port " . VLC2_PORT,
        "--http-password " . Q(VLC_PASS)
    ), PORTRAIT_DIR)
    Log("Startup: launched portrait VLC pid=" . pid2)

    pid3 := RunVLC(Join(
        "--no-one-instance --random --loop",
        "--extraintf http",
        "--http-host 127.0.0.1",
        "--http-port " . VLC3_PORT,
        "--http-password " . Q(VLC_PASS)
    ), LANDSCAPE_DIR)
    Log("Startup: launched landscape VLC pid=" . pid3)

    WaitForHttp(VLC2_PORT, 7000)
    Log("Startup: portrait VLC HTTP ready")
    WaitForHttp(VLC3_PORT, 7000)
    Log("Startup: landscape VLC HTTP ready")

    SetRepeatMode(VLC2_PORT, "all")
    SetRepeatMode(VLC3_PORT, "all")
    Log("Startup: satellite repeat modes configured")

    Sleep 250
    SendVlcCommand(VLC2_PORT, "pl_next")
    Sleep 150
    SendVlcCommand(VLC3_PORT, "pl_next")
    Log("Startup: satellite VLCs advanced to first item")

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
    Log("Startup: Robot Hand sync timer running")

    A_IconTip := "Fun Time Controller"
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Open Controller Log", ShowControllerLog)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Exit Fun Time", (*) => ShutdownAll())
    A_TrayMenu.AddStandard()
}
