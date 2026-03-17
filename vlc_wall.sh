#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# vlc_wall.sh (Git Bash on Windows) -> AutoHotkey v2 controller
###############################################################################

### ===================== CONFIG =====================

VLC_EXE="/c/Program Files/VideoLAN/VLC/vlc.exe"
MFP_EXE="/c/Program Files/MultiFunPlayer-1.33.9-patreon/MultiFunPlayer.exe"
AHK_EXE="/c/Program Files/AutoHotkey/v2/AutoHotkey64.exe"   # AutoHotkey v2

WINSTON_DIR="/c/path/to/suite-root/videos/videos/2D/winston/3_good_to_go"
PORTRAIT_DIR="/c/path/to/suite-root/videos/videos/2D/AI/2_outbox/upscaled_by_orientation/portrait"
LANDSCAPE_DIR="/c/path/to/suite-root/videos/videos/2D/AI/2_outbox/upscaled_by_orientation/landscape"

WEIRD_DIR="/c/path/to/suite-root/videos/videos/2D/AI/2_outbox/kinda_weird"

ROBOT_HAND_PY="/c/Users/Alex/miniconda3/pythonw.exe"
ROBOT_HAND_SCRIPT="/c/path/to/suite-root/projects/osr2_reader/robot_hand_listener.py"
BROKER_SCRIPT="/c/path/to/suite-root/projects/osr2_reader/osr2_broker.py"
ROBOT_HAND_CLIPS="/c/path/to/suite-root/projects/osr2_reader/clips"
ROBOT_HAND_AUDIO_SCRIPT="/c/path/to/suite-root/projects/osr2_reader/robot_hand_audio_companion.py"
ROBOT_HAND_AUDIO="/c/path/to/suite-root/projects/osr2_reader/audio"
ROBOT_MODE_FILE="/c/path/to/suite-root/projects/osr2_reader/robot_mode.txt"
ROBOT_HAND_CMD_FILE="/c/path/to/suite-root/projects/osr2_reader/robot_hand_cmd.txt"

# Favorites CSV (2 columns only, both clickable in LibreOffice Calc)
FAVS_FILE="/c/path/to/suite-root/favs.csv"

VLC2_HTTP_PORT="8091"          # pid2 / portrait
VLC3_HTTP_PORT="8092"          # pid3 / landscape

### ===================== HELPERS =====================

need_file() { [[ -f "$1" ]] || { echo "Missing file: $1" >&2; exit 1; }; }

to_win_path() {
  local p="$1"
  if [[ "$p" =~ ^[A-Za-z]:\\ ]]; then
    printf '%s' "$p"
  else
    cygpath -w "$p"
  fi
}

need_file "$VLC_EXE"
need_file "$MFP_EXE"
need_file "$AHK_EXE"
need_file "$ROBOT_HAND_PY"
need_file "$ROBOT_HAND_SCRIPT"
need_file "$BROKER_SCRIPT"
need_file "$ROBOT_HAND_AUDIO_SCRIPT"

mkdir -p "$WEIRD_DIR"
touch "$FAVS_FILE"

VLC_WIN="$(to_win_path "$VLC_EXE")"
MFP_WIN="$(to_win_path "$MFP_EXE")"
AHK_WIN="$(to_win_path "$AHK_EXE")"

ROBOT_HAND_PY_WIN="$(to_win_path "$ROBOT_HAND_PY")"
ROBOT_HAND_SCRIPT_WIN="$(to_win_path "$ROBOT_HAND_SCRIPT")"
BROKER_SCRIPT_WIN="$(to_win_path "$BROKER_SCRIPT")"
ROBOT_HAND_CLIPS_WIN="$(to_win_path "$ROBOT_HAND_CLIPS")"
ROBOT_HAND_AUDIO_SCRIPT_WIN="$(to_win_path "$ROBOT_HAND_AUDIO_SCRIPT")"
ROBOT_HAND_AUDIO_WIN="$(to_win_path "$ROBOT_HAND_AUDIO")"
ROBOT_MODE_FILE_WIN="$(to_win_path "$ROBOT_MODE_FILE")"
ROBOT_HAND_CMD_FILE_WIN="$(to_win_path "$ROBOT_HAND_CMD_FILE")"

WINSTON_WIN="$(to_win_path "$WINSTON_DIR")"
PORTRAIT_WIN="$(to_win_path "$PORTRAIT_DIR")"
LANDSCAPE_WIN="$(to_win_path "$LANDSCAPE_DIR")"
WEIRD_WIN="$(to_win_path "$WEIRD_DIR")"
FAVS_WIN="$(to_win_path "$FAVS_FILE")"

# Password for VLC HTTP interface (printed so you can test in browser if needed)
VLC_HTTP_PASS="vlcwall_$(date +%s)"

AHK_SCRIPT="./vlc_wall_controller.ahk"
AHK_SCRIPT_WIN="$(cygpath -aw "$AHK_SCRIPT")"

cat > "$AHK_SCRIPT" <<'AHK'
#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon
Persistent
DetectHiddenWindows False
SetTitleMatchMode 2

; Args:
; 1 VLC_EXE, 2 MFP_EXE, 3 WINSTON_DIR, 4 PORTRAIT_DIR, 5 LANDSCAPE_DIR,
; 6 WEIRD_DIR, 7 FAVS_FILE, 8 VLC2_PORT, 9 VLC3_PORT, 10 VLC_PASS,
; 11 ROBOT_HAND_PY, 12 ROBOT_HAND_SCRIPT, 13 BROKER_SCRIPT, 14 ROBOT_HAND_CLIPS,
; 15 ROBOT_HAND_AUDIO_SCRIPT, 16 ROBOT_HAND_AUDIO, 17 ROBOT_MODE_FILE, 18 ROBOT_HAND_CMD_FILE
if (A_Args.Length < 18) {
    MsgBox("Not enough arguments passed to controller. Got " . A_Args.Length, "vlc_wall", "Iconx")
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
ROBOT_HAND_SCRIPT     := A_Args[12]
BROKER_SCRIPT         := A_Args[13]
ROBOT_HAND_CLIPS      := A_Args[14]
ROBOT_HAND_AUDIO_SCRIPT := A_Args[15]
ROBOT_HAND_AUDIO      := A_Args[16]
ROBOT_MODE_FILE       := A_Args[17]
ROBOT_HAND_CMD_FILE   := A_Args[18]

; IMPORTANT: VLC web interface commonly uses BLANK username + password.
VLC_USER := ""

locked2 := false
locked3 := false

robotMode := false

Q(s) => Format('"{1}"', s)

Join(a, b, c := "", d := "", e := "") {
    out := a
    for v in [b,c,d,e] {
        if (v != "")
            out .= " " . v
    }
    return out
}

RunApp(exe, args) {
    cmd := Q(exe)
    if (args != "")
        cmd .= " " . args
    Run(cmd, , , &pid)
    return pid
}

RunVLC(args, mediaPath) {
    cmd := Q(VLC_EXE) . " " . args . " " . Q(mediaPath)
    Run(cmd, , , &pid)
    return pid
}

GetRobotHandRect(&x, &y, &w, &h) {
    ; Match pid1 placement exactly: bottom section of monitor 1
    MonitorGetWorkArea(1, &sL, &sT, &sR, &sB)
    sW := sR - sL
    sH := sB - sT
    hTop := Floor(sH * 8 / 11)
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

RobotModeOn() {
    global ROBOT_MODE_FILE
    try {
        if !FileExist(ROBOT_MODE_FILE)
            return false
        v := Trim(FileRead(ROBOT_MODE_FILE, "UTF-8"))
        return (v = "1")
    } catch {
        return false
    }
}

SyncRobotHandState() {
    global robotMode, pid1

    modeOn := RobotModeOn()

    if (modeOn && !robotMode) {
        robotMode := true
        try ControlSend("{Space}", , "ahk_pid " pid1)   ; pause pid1
    } else if (!modeOn && robotMode) {
        robotMode := false
        try ControlSend("{Space}", , "ahk_pid " pid1)   ; resume pid1
    }

    if modeOn {
        try WinSetAlwaysOnTop(false, "ahk_pid " pid1)
        try WinSetAlwaysOnTop(true, "Robot Hand")
        try WinActivate("Robot Hand")
    } else {
        try WinSetAlwaysOnTop(false, "Robot Hand")
        try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
    }
}

; -------------------- LAUNCH --------------------

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

pidB := RunApp(ROBOT_HAND_PY, Q(BROKER_SCRIPT))

rx := 0, ry := 0, rw := 0, rh := 0
GetRobotHandRect(&rx, &ry, &rw, &rh)

pidR := RunApp(ROBOT_HAND_PY
    , Q(ROBOT_HAND_SCRIPT)
    . " --clips-folder " . Q(ROBOT_HAND_CLIPS)
    . " --reverse"
    . " --x " . rx
    . " --y " . ry
    . " --width " . rw
    . " --height " . rh
)

pidA := RunApp(ROBOT_HAND_PY
    , Q(ROBOT_HAND_AUDIO_SCRIPT)
    . " --audio-folder " . Q(ROBOT_HAND_AUDIO)
)

SetTimer(SyncRobotHandState, 200)

; -------------------- HOTKEYS --------------------

Esc::ShutdownAll(pid1, pid2, pid3, pidM, pidB, pidR, pidA)

Space::SendToPid(pid1, "{Space}")

[::{
    if RobotModeOn() {
        try FileDelete(ROBOT_HAND_CMD_FILE)
        FileAppend("PREV", ROBOT_HAND_CMD_FILE, "UTF-8-RAW")
    } else {
        try ControlSend("p", , "ahk_pid " pid1)
    }
}

]::{
    if RobotModeOn() {
        try FileDelete(ROBOT_HAND_CMD_FILE)
        FileAppend("NEXT", ROBOT_HAND_CMD_FILE, "UTF-8-RAW")
    } else {
        try ControlSend("n", , "ahk_pid " pid1)
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

ShutdownAll(pid1, pid2, pid3, pidM, pidB := 0, pidR := 0, pidA := 0) {
    for pid in [pid1, pid2, pid3, pidM, pidB, pidR, pidA] {
        if (pid) {
            try WinClose("ahk_pid " pid)
        }
    }
    Sleep 400
    for pid in [pid1, pid2, pid3, pidM, pidB, pidR, pidA] {
        if (pid) {
            try ProcessClose(pid)
        }
    }
    ExitApp
}

PositionAll(pid1, pid2, pid3, pidM) {
    MonitorGetWorkArea(2, &pL, &pT, &pR, &pB)
    MonitorGetWorkArea(1, &sL, &sT, &sR, &sB)

    pW := pR - pL, pH := pB - pT
    sW := sR - sL, sH := sB - sT

    hTop := Floor(sH * 8 / 11)
    hBot := sH - hTop

    MovePidWindow(pid2, sL, sT,       sW, hTop)
    MovePidWindow(pid1, sL, sT+hTop,  sW, hBot)
    w3 := Floor(pW * 2 / 3)
    x3 := pL + (pW - w3)   ; right-aligned 2/3
    MovePidWindow(pid3, x3, pT, w3, pH)

    leftW := pW - w3          ; width of the unused left region (≈ 1/3)
    mW := Floor(leftW * 0.90) ; make it ~90% of that region width
    mH := Floor(pH * 0.60)    ; and ~60% of screen height
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
    MsgBox("VLC HTTP interface did not come up on port " . port . "`nControls for that player will not work until this is resolved.", "vlc_wall", "Icon!")
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
        } else {
            SetRepeatMode(port, "all")
            VlcHttpCmd(port, "pl_next")
            locked2 := false
        }
    } else {
        if (!locked3) {
            SetRepeatMode(port, "one")
            EnsureInFavs(GetCurrentFilePath(port))
            locked3 := true
        } else {
            SetRepeatMode(port, "all")
            VlcHttpCmd(port, "pl_next")
            locked3 := false
        }
    }
}
AHK

echo "Starting controller (AutoHotkey v2). Press Esc to close everything."
echo "VLC HTTP password (blank username): $VLC_HTTP_PASS"
echo "AHK script: $AHK_SCRIPT"
echo "Favorites CSV: $FAVS_FILE"

MSYS2_ARG_CONV_EXCL='*' MSYS_NO_PATHCONV=1 "$AHK_WIN" "$AHK_SCRIPT_WIN" \
  "$VLC_WIN" "$MFP_WIN" \
  "$WINSTON_WIN" "$PORTRAIT_WIN" "$LANDSCAPE_WIN" \
  "$WEIRD_WIN" "$FAVS_WIN" \
  "$VLC2_HTTP_PORT" "$VLC3_HTTP_PORT" \
  "$VLC_HTTP_PASS" \
  "$ROBOT_HAND_PY_WIN" "$ROBOT_HAND_SCRIPT_WIN" "$BROKER_SCRIPT_WIN" \
  "$ROBOT_HAND_CLIPS_WIN" "$ROBOT_HAND_AUDIO_SCRIPT_WIN" "$ROBOT_HAND_AUDIO_WIN" \
  "$ROBOT_MODE_FILE_WIN" "$ROBOT_HAND_CMD_FILE_WIN"