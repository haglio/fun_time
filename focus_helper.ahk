#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon
Persistent

; Register for shell events (window created / flash etc.)
SHELLHOOK := DllCall("RegisterWindowMessage", "Str", "SHELLHOOK", "UInt")
DllCall("RegisterShellHookWindow", "Ptr", A_ScriptHwnd)
OnMessage(SHELLHOOK, ShellHookProc)

ShellHookProc(wParam, lParam, *) {
    static HSHELL_WINDOWCREATED := 1
    static HSHELL_FLASH         := 0x8006
    static HSHELL_RUDEAPP       := 0x8004  ; sometimes used for forced activation/attention

    if !(wParam = HSHELL_WINDOWCREATED || wParam = HSHELL_FLASH || wParam = HSHELL_RUDEAPP)
        return

    hwnd := lParam
    if !WinExist("ahk_id " hwnd)
        return

    try exe := WinGetProcessName("ahk_id " hwnd)
    catch
        return

    if (StrLower(exe) != "vlc.exe")
        return

    ; Bring VLC to the front.
    ; If you want it to appear ABOVE always-on-top windows, uncomment the AlwaysOnTop line.
    ; WinSetAlwaysOnTop(true, "ahk_id " hwnd)

    WinActivate("ahk_id " hwnd)
}
