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
            CancelLock(2), SendVlcCommand(VLC2_PORT, "pl_previous")
        case "portrait_next":
            CancelLock(2), SendVlcCommand(VLC2_PORT, "pl_next")
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
            CancelLock(3), SendVlcCommand(VLC3_PORT, "pl_previous")
        case "landscape_next":
            CancelLock(3), SendVlcCommand(VLC3_PORT, "pl_next")
        case "landscape_lock":
            ToggleLock(3)
        case "landscape_trash":
            Discard(3)
        case "link_toggle":
            ToggleRobotHandEnabled()
        case "omnipause_toggle":
            OmniPauseToggle()
        case "fmode_toggle":
            ToggleFMode()
        case "robot_toggle":
            ToggleRobotHandEnabled()
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

SendVlcCommand(port, cmd) {
    global VLC_PASS
    args := "send-command"
        . " --port " . port
        . " --password " . Q(VLC_PASS)
        . " --command " . cmd
    return RunControllerVlcAction(args) = 0
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
    global locked2, locked3, VLC_PASS
    port := (which = 2) ? VLC2_PORT : VLC3_PORT
    currentLocked := (which = 2) ? locked2 : locked3
    planPath := BuildLockPlanPath(which)
    extraArgs := "--port " . port
        . " --password " . Q(VLC_PASS)
    plan := RunControllerLockAction("apply-cancel-lock", which, currentLocked, "", planPath, extraArgs)
    if !IsObject(plan)
        return
    if (which = 2)
        locked2 := plan["next_locked"]
    else
        locked3 := plan["next_locked"]
    UpdateFunTimeDashboard()
}

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

Discard(which) {
    global locked2, locked3, FAVS_FILE, WEIRD_DIR, VLC_PASS
    port := (which = 2) ? VLC2_PORT : VLC3_PORT
    src := GetCurrentFilePath(port)
    currentLocked := (which = 2) ? locked2 : locked3
    planPath := BuildLockPlanPath(which)
    extraArgs := "--port " . port
        . " --password " . Q(VLC_PASS)
        . " --favs-file " . Q(FAVS_FILE)
        . " --weird-dir " . Q(WEIRD_DIR)
    plan := RunControllerLockAction("apply-discard", which, currentLocked, src, planPath, extraArgs)
    if !IsObject(plan)
        return

    if (which = 2)
        locked2 := plan["next_locked"]
    else
        locked3 := plan["next_locked"]
    if (plan["log_message"] != "")
        Log(plan["log_message"])
    UpdateFunTimeDashboard()
}

ToggleLock(which) {
    global locked2, locked3, FAVS_FILE, VLC_PASS
    port := (which = 2) ? VLC2_PORT : VLC3_PORT
    currentLocked := (which = 2) ? locked2 : locked3
    currentPath := GetCurrentFilePath(port)
    planPath := BuildLockPlanPath(which)
    extraArgs := "--port " . port
        . " --password " . Q(VLC_PASS)
        . " --favs-file " . Q(FAVS_FILE)
    plan := RunControllerLockAction("apply-toggle-lock", which, currentLocked, currentPath, planPath, extraArgs)
    if !IsObject(plan)
        return

    if (which = 2)
        locked2 := plan["next_locked"]
    else
        locked3 := plan["next_locked"]
    if (plan["log_message"] != "")
        Log(plan["log_message"])
    UpdateFunTimeDashboard()
}
