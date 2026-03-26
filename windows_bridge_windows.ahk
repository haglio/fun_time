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
    global pid1, pid2, pid3, pidM, pidD, omniPaused, robotHandMode

    shouldLeaveOmniPause := !omniPaused
    if (shouldLeaveOmniPause) {
        DispatchBridgeCommand("enter_omnipause")
        for pid in [pid1, pid2, pid3, pidM, pidD]
            try WinSetAlwaysOnTop(false, "ahk_pid " pid)
    }

    try {
        OpenPrimaryVlcFileDialog()

        if (shouldLeaveOmniPause) {
            dialogSpec := "ahk_class #32770 ahk_pid " pid1
            if WinWait(dialogSpec, , 1.0)
                WinWaitClose(dialogSpec)
        }
    } finally {
        if (shouldLeaveOmniPause) {
            DispatchBridgeCommand("leave_omnipause_skip_primary")
            if (!robotHandMode)
                try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
            try WinSetAlwaysOnTop(true, "ahk_pid " pidD)
            try WinSetAlwaysOnTop(true, "ahk_pid " pid2)
            try WinSetAlwaysOnTop(true, "ahk_pid " pid3)
            try WinSetAlwaysOnTop(true, "ahk_pid " pidM)
            SyncRobotHandState()
        }
    }
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
    global RANDOM_FAVS_BROWSER_ENABLED
    if (!RANDOM_FAVS_BROWSER_ENABLED) {
        try FileDelete(RANDOM_FAVS_BROWSER_MANIFEST_FILE)
        return
    }
    try FileDelete(RANDOM_FAVS_BROWSER_MANIFEST_FILE)
    args := "prepare-random-favs-browser-manifest"
        . " --config " . Q(CONFIG_PATH)
        . " --output " . Q(RANDOM_FAVS_BROWSER_MANIFEST_FILE)
    RunWindowsBridgeStartupAction(args)
}

MaybeLaunchRandomFavsBrowser(pidM) {
    global RANDOM_FAVS_BROWSER_ENABLED, RANDOM_FAVS_BROWSER_MANIFEST_FILE, RANDOM_FAVS_BROWSER_SHORTCUT_PATH
    if (!RANDOM_FAVS_BROWSER_ENABLED)
        return

    existing := GetVisibleChromeWindowSnapshot()
    target := "", workDir := "", args := "", description := "", iconPath := "", iconNum := 0, runState := 0
    try FileGetShortcut(RANDOM_FAVS_BROWSER_SHORTCUT_PATH, &target, &workDir, &args, &description, &iconPath, &iconNum, &runState)
    if !LaunchRandomFavsBrowserViaPython(RANDOM_FAVS_BROWSER_MANIFEST_FILE, target, workDir, args) {
        Log("Random Favs Browser skipped because the launch plan was empty")
        return
    }

    newHwnd := WaitForChromeLaunchWindow(existing, 8000)
    if (!newHwnd) {
        Log("Random Favs Browser skipped because the browser launch command did not produce a usable visible window")
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
    Log("Random Favs Browser positioned using direct launch plan")
}

GetVisibleChromeWindowSnapshot() {
    windows := []
    winList := WinGetList("ahk_exe chrome.exe")
    for hwnd in winList {
        title := ""
        try title := WinGetTitle("ahk_id " hwnd)
        if (Trim(title) = "")
            continue
        windows.Push({hwnd: hwnd, title: title})
    }
    return windows
}

WaitForChromeLaunchWindow(existingWindows, timeoutMs := 8000) {
    started := A_TickCount
    loop {
        current := GetVisibleChromeWindowSnapshot()
        for window in current {
            if !HandleInChromeWindowSnapshot(window.hwnd, existingWindows)
                return window.hwnd
        }
        for window in current {
            previousTitle := GetChromeWindowTitle(window.hwnd, existingWindows)
            if (previousTitle != "" && previousTitle != window.title)
                return window.hwnd
        }
        if (A_TickCount - started > timeoutMs)
            break
        Sleep 200
    }
    return 0
}

HandleInChromeWindowSnapshot(hwnd, windows) {
    for window in windows {
        if (window.hwnd = hwnd)
            return true
    }
    return false
}

GetChromeWindowTitle(hwnd, windows) {
    for window in windows {
        if (window.hwnd = hwnd)
            return window.title
    }
    return ""
}

