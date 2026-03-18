#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon
Persistent
DetectHiddenWindows False
SetTitleMatchMode 2

; Args:
; 1 VLC_EXE, 2 MFP_EXE, 3 WINSTON_DIR, 4 PORTRAIT_DIR, 5 LANDSCAPE_DIR,
; 6 WEIRD_DIR, 7 FAVS_FILE, 8 VLC2_PORT, 9 VLC3_PORT, 10 VLC_PASS,
; 11 ROBOT_HAND_PY, 12 ROBOT_HAND_MODULE, 13 ROBOT_HAND_CLIPS,
; 14 ROBOT_HAND_AUDIO_MODULE, 15 ROBOT_HAND_AUDIO, 16 ROBOT_HAND_MODE_FILE, 17 ROBOT_HAND_CMD_FILE,
; 18 BROKER_CMD_FILE, 19 AUDIO_CMD_FILE, 20 PRIMARY_MONITOR, 21 SECONDARY_MONITOR, 22 PRIMARY_TOP_RATIO,
; 23 LANDSCAPE_WIDTH_RATIO, 24 MFP_WIDTH_RATIO, 25 MFP_HEIGHT_RATIO, 26 CONTROLLER_LOG_FILE, 27 CONFIG_PATH
if (A_Args.Length < 27) {
    MsgBox("Not enough arguments passed to controller. Got " . A_Args.Length, "fun_time", "Iconx")
    ExitApp 2
}

VLC_EXE               := A_Args[1]
MFP_EXE               := A_Args[2]
WINSTON_DIR           := A_Args[3]
PORTRAIT_DIR          := A_Args[4]
LANDSCAPE_DIR         := A_Args[5]
WEIRD_DIR             := A_Args[6]
FAVS_FILE             := A_Args[7]
VLC2_PORT             := A_Args[8]
VLC3_PORT             := A_Args[9]
VLC_PASS              := A_Args[10]
ROBOT_HAND_PY         := A_Args[11]
ROBOT_HAND_MODULE     := A_Args[12]
ROBOT_HAND_CLIPS      := A_Args[13]
ROBOT_HAND_AUDIO_MODULE := A_Args[14]
ROBOT_HAND_AUDIO      := A_Args[15]
ROBOT_HAND_MODE_FILE       := A_Args[16]
ROBOT_HAND_CMD_FILE   := A_Args[17]
BROKER_CMD_FILE       := A_Args[18]
AUDIO_CMD_FILE        := A_Args[19]
PRIMARY_MONITOR       := A_Args[20]
SECONDARY_MONITOR     := A_Args[21]
PRIMARY_TOP_RATIO     := A_Args[22]
LANDSCAPE_WIDTH_RATIO := A_Args[23]
MFP_WIDTH_RATIO       := A_Args[24]
MFP_HEIGHT_RATIO      := A_Args[25]
CONTROLLER_LOG_FILE   := A_Args[26]
CONFIG_PATH           := A_Args[27]

PROJECT_DIR := ""
SplitPath(CONFIG_PATH, , &PROJECT_DIR)
if (PROJECT_DIR = "")
    PROJECT_DIR := A_ScriptDir

; IMPORTANT: VLC web interface commonly uses BLANK username + password.
VLC_USER := ""

locked2 := false
locked3 := false

robotHandMode := false
omniPaused := false

Q(s) => Format('"{1}"', s)

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

RunApp(exe, args) {
    global PROJECT_DIR
    cmd := Q(exe)
    if (args != "")
        cmd .= " " . args
    Run(cmd, PROJECT_DIR, , &pid)
    return pid
}

RunVLC(args, mediaPath) {
    cmd := Q(VLC_EXE) . " " . args . " " . Q(mediaPath)
    Run(cmd, , , &pid)
    return pid
}

GetRobotHandRect(&x, &y, &w, &h) {
    global PRIMARY_MONITOR, PRIMARY_TOP_RATIO
    MonitorGetWorkArea(PRIMARY_MONITOR, &sL, &sT, &sR, &sB)
    sW := sR - sL
    sH := sB - sT
    hTop := Floor(sH * Clamp01(PRIMARY_TOP_RATIO))
    hBot := sH - hTop

    x := sL
    y := sT + hTop
    w := sW
    h := hBot
}

SendToPid(pid, keys) {
    try ControlSend(keys, , "ahk_pid " pid)
}

SendToTitle(title, keys) {
    try ControlSend(keys, , title)
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

SyncRobotHandState() {
    global robotHandMode, pid1, omniPaused

    if (omniPaused)
        return

    modeState := RobotHandModeState()
    modeOn := (modeState = "1")

    if (modeOn && !robotHandMode) {
        robotHandMode := true
        Log("Entering Robot Hand mode")
        try ControlSend("{Space}", , "ahk_pid " pid1)   ; pause pid1
        try WinSetAlwaysOnTop(false, "ahk_pid " pid1)
        try WinSetAlwaysOnTop(true, "Robot Hand")
        try WinActivate("Robot Hand")
    } else if (!modeOn && robotHandMode) {
        robotHandMode := false
        Log("Leaving Robot Hand mode")
        try WinSetAlwaysOnTop(false, "Robot Hand")
        try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
        if (modeState = "0") {
            try ControlSend("{Space}", , "ahk_pid " pid1)   ; only resume on normal exit
        }
    }
}

; -------------------- LAUNCH --------------------

Log("Controller starting")

pid1 := RunVLC("--no-one-instance --random --repeat", WINSTON_DIR)
Sleep 900
SendToPid(pid1, "n")

pidM := RunApp(MFP_EXE, "")
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

PositionAll(pid1, pid2, pid3, pidM)
SetTopMost(pid1, pid2, pid3)

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
    . " --cmd-file " . Q(AUDIO_CMD_FILE)
)
Log("Started Robot Hand audio pid=" . pidA)

SetTimer(SyncRobotHandState, 200)

; -------------------- HOTKEYS --------------------

#SuspendExempt true
q::ShutdownAll(pid1, pid2, pid3, pidM, pidR, pidA)
Esc::OmniPauseToggle()
#SuspendExempt false

[::{
    if (RobotHandModeState() = "1") {
        try FileDelete(ROBOT_HAND_CMD_FILE)
        FileAppend("PREV", ROBOT_HAND_CMD_FILE, "UTF-8-RAW")
    } else {
        try ControlSend("p", , "ahk_pid " pid1)
    }
}

]::{
    if (RobotHandModeState() = "1") {
        try FileDelete(ROBOT_HAND_CMD_FILE)
        FileAppend("NEXT", ROBOT_HAND_CMD_FILE, "UTF-8-RAW")
    } else {
        try ControlSend("n", , "ahk_pid " pid1)
    }
}

\::{
    if (RobotHandModeState() = "1") {
        try FileDelete(ROBOT_HAND_CMD_FILE)
        FileAppend("NUDGE25", ROBOT_HAND_CMD_FILE, "UTF-8-RAW")
    }
}

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

ShutdownAll(pid1, pid2, pid3, pidM, pidR := 0, pidA := 0) {
    Log("Shutdown requested")
    for pid in [pid1, pid2, pid3, pidM, pidR, pidA] {
        if (pid) {
            try WinClose("ahk_pid " pid)
        }
    }
    Sleep 400
    for pid in [pid1, pid2, pid3, pidM, pidR, pidA] {
        if (pid) {
            try ProcessClose(pid)
        }
    }
    ExitApp
}

PositionAll(pid1, pid2, pid3, pidM) {
    global PRIMARY_MONITOR, SECONDARY_MONITOR, PRIMARY_TOP_RATIO, LANDSCAPE_WIDTH_RATIO, MFP_WIDTH_RATIO, MFP_HEIGHT_RATIO
    MonitorGetWorkArea(SECONDARY_MONITOR, &pL, &pT, &pR, &pB)
    MonitorGetWorkArea(PRIMARY_MONITOR, &sL, &sT, &sR, &sB)

    pW := pR - pL, pH := pB - pT
    sW := sR - sL, sH := sB - sT

    hTop := Floor(sH * Clamp01(PRIMARY_TOP_RATIO))
    hBot := sH - hTop

    MovePidWindow(pid2, sL, sT,       sW, hTop)
    MovePidWindow(pid1, sL, sT+hTop,  sW, hBot)
    w3 := Floor(pW * Clamp01(LANDSCAPE_WIDTH_RATIO))
    x3 := pL + (pW - w3)   ; right-aligned 2/3
    MovePidWindow(pid3, x3, pT, w3, pH)

    leftW := pW - w3          ; width of the unused left region (≈ 1/3)
    mW := Floor(leftW * Clamp01(MFP_WIDTH_RATIO))
    mH := Floor(pH * Clamp01(MFP_HEIGHT_RATIO))
    mX := pL + Floor((leftW - mW) / 2)
    mY := pT + Floor((pH - mH) / 2)
    MovePidWindow(pidM, mX, mY, mW, mH)
}

MovePidWindow(pid, x, y, w, h) {
    hwnd := WinWait("ahk_pid " pid, , 10)
    WinRestore("ahk_id " hwnd)
    WinMove(x, y, w, h, "ahk_id " hwnd)
}

SetTopMost(pid1, pid2, pid3) {
    for pid in [pid1, pid2, pid3] {
        try {
            hwnd := WinExist("ahk_pid " pid)
            if (hwnd) {
                WinSetAlwaysOnTop(true, "ahk_id " hwnd)
                WinActivate("ahk_id " hwnd)
            }
        }
    }
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

    if (which = 2 && locked2) {
        SetRepeatMode(port, "all")
        locked2 := false
    } else if (which = 3 && locked3) {
        SetRepeatMode(port, "all")
        locked3 := false
    }
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

; -------------------- Favorites CSV (2 columns, clickable formulas) --------------------

; LibreOffice formula separator:
; - Most locales use ";"  (keep as-is unless your Calc expects ",")
FORMULA_SEP := ";"

CsvEscape(s) {
    s := StrReplace(s, '"', '""')
    return '"' . s . '"'
}

EnsureFavsCsvExists() {
    if FileExist(FAVS_FILE) && FileGetSize(FAVS_FILE) > 0
        return
    FileAppend("local_file,web_url`r`n", FAVS_FILE, "UTF-8")
}

ToFileUri(winPath) {
    if (winPath = "")
        return ""
    p := StrReplace(winPath, "\", "/")
    p := StrReplace(p, " ", "%20")
    return "file:///" . p
}

MakeWebUrlFromPath(fullPath) {
    if (fullPath = "")
        return ""

    SplitPath(fullPath, &name, , , &nameNoExt)
    id := RegExReplace(nameNoExt, "_[^_]+$", "")

    if InStr(fullPath, "\provider2\")
        return "https://example.net/image/" . id
    if InStr(fullPath, "\provider\")
        return "https://example.com/image/" . id

    return ""
}

MakeLocalCell(fullPath) {
    global FORMULA_SEP
    if (fullPath = "")
        return ""
    uri := ToFileUri(fullPath)
    q := Chr(34)
    return "=HYPERLINK(" . q . uri . q . FORMULA_SEP . q . fullPath . q . ")"
}

MakeWebCell(fullPath) {
    global FORMULA_SEP
    url := MakeWebUrlFromPath(fullPath)
    if (url = "")
        return ""
    q := Chr(34)
    return "=HYPERLINK(" . q . url . q . FORMULA_SEP . q . url . q . ")"
}

EnsureInFavs(fullPath) {
    if (fullPath = "")
        return

    EnsureFavsCsvExists()

    localCell := MakeLocalCell(fullPath)
    webCell   := MakeWebCell(fullPath)

    content := FileRead(FAVS_FILE, "UTF-8")

    needle := CsvEscape(localCell) . ","
    if InStr(content, needle)
        return

    row := CsvEscape(localCell) . "," . CsvEscape(webCell) . "`r`n"
    FileAppend(row, FAVS_FILE, "UTF-8")
}

RemoveFromFavs(fullPath) {
    if (fullPath = "")
        return
    if !FileExist(FAVS_FILE)
        return

    targetLocal := MakeLocalCell(fullPath)
    targetPrefix := CsvEscape(targetLocal) . ","

    content := FileRead(FAVS_FILE, "UTF-8")
    lines := StrSplit(content, "`n", "`r")
    out := ""

    for line in lines {
        if (line = "")
            continue

        if (InStr(line, "local_file,web_url") = 1) {
            out .= line . "`r`n"
            continue
        }

        if (InStr(line, targetPrefix) = 1)
            continue

        out .= line . "`r`n"
    }

    FileDelete(FAVS_FILE)
    FileAppend(out, FAVS_FILE, "UTF-8")
}

; -------------------- Weird move + actions --------------------

MoveToWeird(srcPath) {
    if (srcPath = "")
        return
    try DirCreate(WEIRD_DIR)

    SplitPath(srcPath, &name, , &ext, &nameNoExt)
    dest := WEIRD_DIR . "\" . name

    if FileExist(dest) {
        i := 1
        loop {
            dest := WEIRD_DIR . "\" . nameNoExt . "__dup" . i . "." . ext
            if !FileExist(dest)
                break
            i += 1
        }
    }

    tries := 0
    while (tries < 25) {
        try {
            FileMove(srcPath, dest, false)
            return
        } catch {
            Sleep 120
            tries += 1
        }
    }
}

Discard(which) {
    global locked2, locked3
    port := (which = 2) ? VLC2_PORT : VLC3_PORT
    src := GetCurrentFilePath(port)

    Log("Discarding from player " . which . ": " . src)

    if (which = 2 && locked2) {
        SetRepeatMode(port, "all")
        locked2 := false
    } else if (which = 3 && locked3) {
        SetRepeatMode(port, "all")
        locked3 := false
    }

    RemoveFromFavs(src)

    VlcHttpCmd(port, "pl_next")
    Sleep 250
    MoveToWeird(src)
}

ToggleLock(which) {
    global locked2, locked3
    port := (which = 2) ? VLC2_PORT : VLC3_PORT

    if (which = 2) {
        if (!locked2) {
            SetRepeatMode(port, "one")
            EnsureInFavs(GetCurrentFilePath(port))
            locked2 := true
            Log("Locked portrait VLC")
        } else {
            SetRepeatMode(port, "all")
            VlcHttpCmd(port, "pl_next")
            locked2 := false
            Log("Unlocked portrait VLC")
        }
    } else {
        if (!locked3) {
            SetRepeatMode(port, "one")
            EnsureInFavs(GetCurrentFilePath(port))
            locked3 := true
            Log("Locked landscape VLC")
        } else {
            SetRepeatMode(port, "all")
            VlcHttpCmd(port, "pl_next")
            locked3 := false
            Log("Unlocked landscape VLC")
        }
    }
}

; -------------------- OmniPause --------------------

WriteCmd(file, cmd) {
    try FileDelete(file)
    FileAppend(cmd, file, "UTF-8-RAW")
}

IsOurWindow() {
    global pid1, pid2, pid3, pidM, pidR
    for pid in [pid1, pid2, pid3, pidM, pidR] {
        if WinActive("ahk_pid " pid)
            return true
    }
    if WinActive("Robot Hand")
        return true
    return false
}

OmniPauseToggle() {
    global omniPaused
    if (!omniPaused) {
        EnterOmniPause()
    } else if (IsOurWindow()) {
        LeaveOmniPause()
    }
}

EnterOmniPause() {
    global omniPaused, robotHandMode, pid1, pid2, pid3
    global VLC2_PORT, VLC3_PORT, ROBOT_HAND_CMD_FILE, AUDIO_CMD_FILE

    omniPaused := true
    Log("OmniPause: entering")

    if (robotHandMode) {
        ; Auto mode: VLC1 is already paused by Robot Hand mode; pause VLC2+3, freeze Robot Hand, and pause audio
        VlcHttpCmd(VLC2_PORT, "pl_pause")
        VlcHttpCmd(VLC3_PORT, "pl_pause")
        WriteCmd(ROBOT_HAND_CMD_FILE, "PAUSE")
        WriteCmd(AUDIO_CMD_FILE, "PAUSE")
        try WinSetAlwaysOnTop(false, "Robot Hand")
    } else {
        ; Controlled mode: pause all 3 VLCs
        try ControlSend("{Space}", , "ahk_pid " pid1)
        VlcHttpCmd(VLC2_PORT, "pl_pause")
        VlcHttpCmd(VLC3_PORT, "pl_pause")
    }

    ; Remove always-on-top from all VLC windows so they stop blocking other windows
    for pid in [pid1, pid2, pid3] {
        try WinSetAlwaysOnTop(false, "ahk_pid " pid)
    }

    Suspend true
}

LeaveOmniPause() {
    global omniPaused, robotHandMode, pid1, pid2, pid3
    global VLC2_PORT, VLC3_PORT, ROBOT_HAND_CMD_FILE, AUDIO_CMD_FILE

    Log("OmniPause: leaving")
    Suspend false

    if (robotHandMode) {
        ; Auto mode: resume Robot Hand animation, resume audio, and resume VLC2+3
        WriteCmd(ROBOT_HAND_CMD_FILE, "RESUME")
        WriteCmd(AUDIO_CMD_FILE, "RESUME")
        VlcHttpCmd(VLC2_PORT, "pl_pause")  ; toggle back to playing
        VlcHttpCmd(VLC3_PORT, "pl_pause")
    } else {
        ; Controlled mode: resume all 3 VLCs and restore VLC1 always-on-top
        try ControlSend("{Space}", , "ahk_pid " pid1)
        VlcHttpCmd(VLC2_PORT, "pl_pause")  ; toggle back to playing
        VlcHttpCmd(VLC3_PORT, "pl_pause")
        try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
    }

    ; Restore always-on-top for the two secondary VLC windows
    try WinSetAlwaysOnTop(true, "ahk_pid " pid2)
    try WinSetAlwaysOnTop(true, "ahk_pid " pid3)

    ; Allow SyncRobotHandState to run again and handle any mode transitions that
    ; occurred while paused (e.g. OSR2 exited freemode after receiving neutral pos)
    omniPaused := false
    SyncRobotHandState()

    ; If still in auto mode after the sync check, restore Robot Hand always-on-top
    if (robotHandMode) {
        try WinSetAlwaysOnTop(true, "Robot Hand")
        try WinActivate("Robot Hand")
    }
}
