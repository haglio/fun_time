ProcessDashboardCommand() {
    global DASHBOARD_CMD_FILE
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
    if (action = "omnipause_toggle") {
        HandleOmniPauseToggle()
    } else {
        DispatchBridgeCommand(action)
    }
}

HandleOmniPauseToggle() {
    global omniPaused, pid1, pid2, pid3, pidM, pidD, robotHandMode
    wasOmniPaused := omniPaused
    DispatchBridgeCommand("omnipause_toggle")
    if (!wasOmniPaused) {
        ; Entering omnipause — remove topmost from all media windows
        for pid in [pid1, pid2, pid3, pidM, pidD]
            try WinSetAlwaysOnTop(false, "ahk_pid " pid)
    } else {
        ; Leaving omnipause — restore topmost for media windows
        if (!robotHandMode)
            try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
        try WinSetAlwaysOnTop(true, "ahk_pid " pidD)
        try WinSetAlwaysOnTop(true, "ahk_pid " pid2)
        try WinSetAlwaysOnTop(true, "ahk_pid " pid3)
        try WinSetAlwaysOnTop(true, "ahk_pid " pidM)
        SyncRobotHandState()
    }
}

DispatchBridgeCommand(cmd) {
    Critical  ; Prevent timer threads (e.g. SyncRobotHandState) from interrupting mid-dispatch
    global ROBOT_HAND_PY, BRIDGE_COMMAND_DISPATCH_MODULE, PROJECT_DIR, CONFIG_PATH, VLC_PASS
    global DASHBOARD_ENABLED, DASHBOARD_STATE_FILE
    global locked2, locked3, robotHandMode, fModeEnabled, omniPaused
    global pid1, pidM

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

Base64EncodeUtf8(s) {
    byteLen := StrPut(s, "UTF-8") - 1
    buf := Buffer(byteLen, 0)
    StrPut(s, buf, "UTF-8")
    DllCall("Crypt32\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", byteLen, "UInt", 0x1, "Ptr", 0, "UIntP", &outChars := 0)
    out := Buffer(outChars * 2, 0)
    DllCall("Crypt32\CryptBinaryToStringW", "Ptr", buf.Ptr, "UInt", byteLen, "UInt", 0x1, "Ptr", out.Ptr, "UIntP", &outChars)
    return StrGet(out, outChars, "UTF-16")
}
