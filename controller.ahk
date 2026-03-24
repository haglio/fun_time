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
robotHandStatusGui := ""
robotHandStatusText := ""

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

GetRobotHandStatusRect(&x, &y, &w, &h) {
    GetMfpRect(&mX, &mY, &mW, &mH)
    margin := 14
    x := mX
    y := Max(0, mY - 64)
    w := Max(220, mW)
    h := 52
}

CreateRobotHandStatusGui() {
    global robotHandStatusGui, robotHandStatusText
    guiObj := Gui("+AlwaysOnTop -Caption +ToolWindow", "Fun Time Status")
    guiObj.BackColor := "20262C"
    guiObj.SetFont("s10 Bold", "Segoe UI")
    textCtrl := guiObj.AddText("Center cFFFFFF w260 h36", "")
    robotHandStatusGui := guiObj
    robotHandStatusText := textCtrl
    GetRobotHandStatusRect(&x, &y, &w, &h)
    textCtrl.Move(, , w - 20, 36)
    guiObj.Show("NA x" . x . " y" . y . " w" . w . " h" . h)
    UpdateRobotHandStatusIndicator()
}

UpdateRobotHandStatusIndicator() {
    global robotHandStatusGui, robotHandStatusText, fModeEnabled
    if (!IsObject(robotHandStatusGui) || !IsObject(robotHandStatusText))
        return

    if (RobotHandEnabled()) {
        robotHandStatusGui.BackColor := "1F4D2E"
        robotHandStatusText.Opt("+cFFFFFF")
        robotHandStatusText.Text := "Robot Hand: Enabled`nF-Mode: " . (fModeEnabled ? "On" : "Off")
    } else {
        robotHandStatusGui.BackColor := "6C1F1F"
        robotHandStatusText.Opt("+cFFFFFF")
        robotHandStatusText.Text := "Robot Hand: Disabled`nF-Mode: " . (fModeEnabled ? "On" : "Off")
    }

    GetRobotHandStatusRect(&x, &y, &w, &h)
    robotHandStatusText.Move(, , w - 20, 36)
    robotHandStatusGui.Show("NA x" . x . " y" . y . " w" . w . " h" . h)
}

EnforceRobotHandOutputs(active, isTransition := false) {
    global pid1

    if (active) {
        EnsurePrimaryVlcPlayback(false)
        SetRobotHandPaused(false)
        SetRobotHandAudioPaused(false)
        try WinShow("Robot Hand")
        try WinSetAlwaysOnTop(false, "ahk_pid " pid1)
        try WinSetAlwaysOnTop(true, "Robot Hand")
        try WinActivate("Robot Hand")
    } else {
        SetRobotHandPaused(true)
        SetRobotHandAudioPaused(true)
        try WinHide("Robot Hand")
        try WinSetAlwaysOnTop(false, "Robot Hand")
        try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
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

    UpdateRobotHandStatusIndicator()

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
    enabled := !RobotHandEnabled()
    SetRobotHandEnabled(enabled)

    if (enabled) {
        Log("Robot Hand hotkey: enabled")
    } else {
        Log("Robot Hand hotkey: disabled")
    }

    SyncRobotHandState()
}

IsSupportedVideoPath(path) {
    SplitPath(path, , , &ext)
    ext := "." . StrLower(ext)
    return ext = ".mp4" || ext = ".mkv" || ext = ".mov" || ext = ".avi" || ext = ".webm" || ext = ".m4v"
}

NormalizePathKey(path) {
    return StrLower(Trim(path))
}

CollectVideoFiles(sourceSpec) {
    files := []
    seen := Map()
    for sourcePart in StrSplit(sourceSpec, "|") {
        rootPath := Trim(sourcePart)
        if (rootPath = "")
            continue

        if DirExist(rootPath) {
            Loop Files, rootPath . "\*.*", "FR" {
                fullPath := A_LoopFileFullPath
                if !IsSupportedVideoPath(fullPath)
                    continue
                key := NormalizePathKey(fullPath)
                if seen.Has(key)
                    continue
                seen[key] := true
                files.Push(fullPath)
            }
            continue
        }

        if FileExist(rootPath) && IsSupportedVideoPath(rootPath) {
            key := NormalizePathKey(rootPath)
            if !seen.Has(key) {
                seen[key] := true
                files.Push(rootPath)
            }
        }
    }
    return files
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

ShufflePaths(paths) {
    if (paths.Length <= 1)
        return paths
    idx := paths.Length
    while (idx > 1) {
        swapIdx := Random(1, idx)
        if (swapIdx != idx) {
            temp := paths[idx]
            paths[idx] := paths[swapIdx]
            paths[swapIdx] := temp
        }
        idx -= 1
    }
    return paths
}

BuildPrimaryPlaylistPaths(fMode) {
    global PRIMARY_VLC_SOURCES
    files := CollectVideoFiles(PRIMARY_VLC_SOURCES)
    if !fMode
        return ShufflePaths(files)

    filtered := []
    for fullPath in files {
        if HasMatchingFunscript(fullPath)
            filtered.Push(fullPath)
    }
    return ShufflePaths(filtered)
}

BuildSatellitePlaylistPaths(sourceSpec, fMode) {
    files := CollectVideoFiles(sourceSpec)
    if !fMode
        return ShufflePaths(files)

    favsContent := ReadFavsContent()
    filtered := []
    for fullPath in files {
        if IsFavoritePath(fullPath, favsContent)
            filtered.Push(fullPath)
    }
    return ShufflePaths(filtered)
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

WritePlaylistFile(path, paths) {
    content := "#EXTM3U`r`n"
    for fullPath in paths
        content .= fullPath . "`r`n"
    WriteRawStateFile(path, content)
}

ReplaceVlcPlaylist(port, paths, playlistName, repeatMode := "") {
    if (paths.Length = 0)
        return false

    playlistPath := BuildPlaylistFilePath(playlistName)
    WritePlaylistFile(playlistPath, paths)
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
    global PRIMARY_VLC_PORT, VLC2_PORT, VLC3_PORT, PORTRAIT_DIR, LANDSCAPE_DIR, locked2, locked3

    primaryPaths := BuildPrimaryPlaylistPaths(enabled)
    portraitPaths := BuildSatellitePlaylistPaths(PORTRAIT_DIR, enabled)
    landscapePaths := BuildSatellitePlaylistPaths(LANDSCAPE_DIR, enabled)
    Log("F-mode playlist sizes: primary=" . primaryPaths.Length . " portrait=" . portraitPaths.Length . " landscape=" . landscapePaths.Length)

    if (primaryPaths.Length = 0 || portraitPaths.Length = 0 || landscapePaths.Length = 0) {
        Log("F-mode toggle aborted because one or more playlists would be empty")
        return false
    }

    locked2 := false
    locked3 := false

    if !ReplaceVlcPlaylist(PRIMARY_VLC_PORT, primaryPaths, "primary_vlc_playlist")
        return false
    if !ReplaceVlcPlaylist(VLC2_PORT, portraitPaths, "portrait_vlc_playlist", "all")
        return false
    if !ReplaceVlcPlaylist(VLC3_PORT, landscapePaths, "landscape_vlc_playlist", "all")
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
    UpdateRobotHandStatusIndicator()
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
CreateRobotHandStatusGui()

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
    global isShuttingDown, pid1, pid2, pid3, pidM, pidR, pidA, robotHandStatusGui
    if (isShuttingDown)
        return
    isShuttingDown := true
    Log("Shutdown requested")
    SetTimer(SyncRobotHandState, 0)
    try {
        if (IsObject(robotHandStatusGui))
            robotHandStatusGui.Destroy()
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

    GetMfpRect(&mX, &mY, &mW, &mH)
    MovePidWindow(pidM, mX, mY, mW, mH)
}

GetMfpRect(&x, &y, &w, &h) {
    global LANDSCAPE_WIDTH_RATIO, MFP_WIDTH_RATIO, MFP_HEIGHT_RATIO
    mainRect := "", secondaryRect := ""
    GetLogicalMonitorRects(&mainRect, &secondaryRect)
    mainL := mainRect["x"], mainT := mainRect["y"], mainW := mainRect["w"], mainH := mainRect["h"]
    landscapeW := Floor(mainW * Clamp01(LANDSCAPE_WIDTH_RATIO))
    leftW := mainW - landscapeW
    w := Floor(leftW * Clamp01(MFP_WIDTH_RATIO))
    h := Floor(mainH * Clamp01(MFP_HEIGHT_RATIO))
    x := mainL + Floor((leftW - w) / 2)
    y := mainT + Floor((mainH - h) / 2)
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
    WriteRawStateFile(file, cmd)
}

OmniPauseToggle() {
    global omniPaused
    if (!omniPaused) {
        EnterOmniPause()
    } else {
        LeaveOmniPause()
    }
}

EnterOmniPause() {
    global omniPaused, robotHandMode, pid1, pid2, pid3, pidM
    global VLC2_PORT, VLC3_PORT

    omniPaused := true
    Log("OmniPause: entering")

    if (robotHandMode) {
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

    ; Remove always-on-top from all VLC windows and MFP so they stop blocking other windows
    for pid in [pid1, pid2, pid3, pidM] {
        try WinSetAlwaysOnTop(false, "ahk_pid " pid)
    }

    Suspend true
}

LeaveOmniPause(skipPrimaryVlcPlaybackToggleOnResume := false) {
    global omniPaused, robotHandMode, pid1, pid2, pid3, pidM
    global VLC2_PORT, VLC3_PORT

    Log("OmniPause: leaving")
    Suspend false

    if (robotHandMode) {
        ; Auto mode: resume Robot Hand animation, resume audio, and resume VLC2+3
        SetRobotHandPaused(false)
        SetRobotHandAudioPaused(false)
        VlcHttpCmd(VLC2_PORT, "pl_pause")  ; toggle back to playing
        VlcHttpCmd(VLC3_PORT, "pl_pause")
    } else {
        ; Controlled mode: resume all 3 VLCs and restore VLC1 always-on-top
        if (!skipPrimaryVlcPlaybackToggleOnResume)
            EnsurePrimaryVlcPlayback(true)
        VlcHttpCmd(VLC2_PORT, "pl_pause")  ; toggle back to playing
        VlcHttpCmd(VLC3_PORT, "pl_pause")
        try WinSetAlwaysOnTop(true, "ahk_pid " pid1)
    }

    ; Restore always-on-top for the two secondary VLC windows and MFP
    try WinSetAlwaysOnTop(true, "ahk_pid " pid2)
    try WinSetAlwaysOnTop(true, "ahk_pid " pid3)
    try WinSetAlwaysOnTop(true, "ahk_pid " pidM)

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
