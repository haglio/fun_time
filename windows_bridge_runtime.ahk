ShowWindowsBridgeLog(*) {
    global WINDOWS_BRIDGE_LOG_FILE
    Run('notepad.exe "' . WINDOWS_BRIDGE_LOG_FILE . '"')
}

HandleWindowsBridgeExit(exitReason, exitCode) {
    global isShuttingDown
    if (isShuttingDown)
        return
    Log("Windows bridge exiting unexpectedly reason=" . exitReason . " code=" . exitCode)
    ShutdownAll()
}

SyncRobotHandState() {
    DispatchBridgeCommand("sync_robot_hand")
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

StartWindowsBridge() {
    global ICON_PATH, DASHBOARD_ENABLED, DASHBOARD_CMD_FILE, DASHBOARD_STATE_FILE
    global pid1, pid2, pid3, pidM, pidD, pidR, pidA
    global WINDOWS_BRIDGE_MANIFEST_PATH

    Log("Windows bridge starting")
    if FileExist(ICON_PATH)
        TraySetIcon(ICON_PATH)

    OnExit(HandleWindowsBridgeExit)

    coreResultPath := BuildStartupResultPath()
    args := "start-core-session"
        . " --manifest " . Q(WINDOWS_BRIDGE_MANIFEST_PATH)
        . " --result-file " . Q(coreResultPath)
    if (RunWindowsBridgeStartupAction(args) != 0)
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
    Log("Startup: broker restarted, Robot Hand state seeded, browser manifest prepared, and core media stack launched")

    WinWait("ahk_pid " pidM, , 15)
    Sleep 5000
    Log("Startup: MFP window ready")

    PositionAll(pid1, pid2, pid3, pidM)
    SetTopMost(pid1, pid2, pid3, pidM)
    Log("Startup: core windows positioned and topmost set")
    MaybeLaunchRandomFavsBrowser(pidM)

    try FileDelete(DASHBOARD_CMD_FILE)
    if (!DASHBOARD_ENABLED)
        try FileDelete(DASHBOARD_STATE_FILE)

    GetCurrentWindowLayout(&plan)
    dashboardX := plan["dashboard"]["x"]
    dashboardY := plan["dashboard"]["y"]
    dashboardW := plan["dashboard"]["w"]
    dashboardH := plan["dashboard"]["h"]

    Sleep 1200

    rx := 0, ry := 0, rw := 0, rh := 0
    GetRobotHandRect(&rx, &ry, &rw, &rh)
    Log("Startup: resolved Robot Hand rect x=" . rx . " y=" . ry . " w=" . rw . " h=" . rh)

    uiResultPath := BuildStartupResultPath()
    args := "launch-ui-companions"
        . " --manifest " . Q(WINDOWS_BRIDGE_MANIFEST_PATH)
        . " --dashboard-x " . dashboardX
        . " --dashboard-y " . dashboardY
        . " --dashboard-width " . dashboardW
        . " --dashboard-height " . dashboardH
        . " --mfp-pid " . pidM
        . " --robot-x " . rx
        . " --robot-y " . ry
        . " --robot-width " . rw
        . " --robot-height " . rh
        . " --result-file " . Q(uiResultPath)
    if (RunWindowsBridgeStartupAction(args) != 0)
        throw Error("Failed to launch UI/runtime companions")
    startupResult := LoadStartupActionResult(uiResultPath)
    if !IsObject(startupResult)
        throw Error("Failed to read UI/runtime companion pids")
    pidD := startupResult["dashboard_pid"]
    pidR := startupResult["robot_hand_pid"]
    pidA := startupResult["audio_pid"]
    if (DASHBOARD_ENABLED) {
        Log("Startup: launched dashboard pid=" . pidD)
    } else {
        Log("Startup: dashboard disabled")
    }
    SetTimer(ProcessDashboardCommand, 150)
    Log("Started Robot Hand listener pid=" . pidR)
    Log("Started Robot Hand audio pid=" . pidA)

    SetTimer(SyncRobotHandState, 200)
    Log("Startup: Robot Hand sync timer running")

    A_IconTip := "Fun Time Windows Bridge"
    A_TrayMenu.Delete()
    A_TrayMenu.Add("Open Windows Bridge Log", ShowWindowsBridgeLog)
    A_TrayMenu.Add()
    A_TrayMenu.Add("Exit Fun Time", (*) => ShutdownAll())
    A_TrayMenu.AddStandard()
}
