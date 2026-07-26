Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' FunTimeVR runs on the project venv, never on whatever python happens to be on
' PATH -- the same trap launch.vbs documents: the sibling packages exist only as
' editable installs in .venv, and a PATH python dies importing them before any
' logging exists.
pythonExe = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")
If Not fso.FileExists(pythonExe) Then
  MsgBox "FunTimeVR's virtual environment is missing:" & vbCrLf & pythonExe, vbCritical, "FunTimeVR"
  WScript.Quit 1
End If

' Everything the orchestrator writes to its console goes here. The launcher runs
' it in a hidden window, and a failure during import happens before any log file
' exists. Overwritten each launch: it holds this launch's crash, not a history.
stateDir = fso.BuildPath(scriptDir, "state")
If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir
launchLog = fso.BuildPath(stateDir, "vr_launcher.log")

' Two sentinels let the hidden launch report its own outcome -- FunTimeVR's own
' pair, so a desktop launch's leftovers can never vouch for a VR launch (and
' vice versa). readyFile lands once config validates and the session commits;
' exitedFlag lands the instant the child exits, however it exits.
readyFile = fso.BuildPath(stateDir, "vr_launcher.ready")
exitedFlag = fso.BuildPath(stateDir, "vr_launcher.exited")
If fso.FileExists(readyFile) Then fso.DeleteFile readyFile
If fso.FileExists(exitedFlag) Then fso.DeleteFile exitedFlag

cmd = "cmd /c cd /d """ & scriptDir & """ && """ & pythonExe & """ -m fun_time_vr.orchestrator > """ & launchLog & """ 2>&1 & type nul > """ & exitedFlag & """"
shell.Run cmd, 0, False

' Watch the sentinels. A good launch drops readyFile within a second or two; a
' crash trips exitedFlag first; a launch wedged before it can do either trips the
' timeout. Every failure path pops the log so the user sees why nothing appeared.
pollMs = 250
maxWaitMs = 45000
waited = 0
started = False
Do
  If fso.FileExists(readyFile) Then
    started = True
    Exit Do
  End If
  If fso.FileExists(exitedFlag) Then Exit Do
  If waited >= maxWaitMs Then Exit Do
  WScript.Sleep pollMs
  waited = waited + pollMs
Loop

If Not started Then
  msg = "FunTimeVR failed to start." & vbCrLf & vbCrLf & _
        "See the full log at:" & vbCrLf & launchLog
  tail = LastLinesOf(launchLog, 15)
  If Len(tail) > 0 Then msg = msg & vbCrLf & vbCrLf & "Last lines of the log:" & vbCrLf & tail
  MsgBox msg, vbCritical, "FunTimeVR"
End If

' Return the tail of a text file (up to maxLines non-blank-terminated lines),
' so the failure dialog can show the traceback that landed in the log without
' making the user go open it.
Function LastLinesOf(path, maxLines)
  Dim out : out = ""
  If fso.FileExists(path) Then
    Dim ts, body, parts, hi, lo, i
    Set ts = fso.OpenTextFile(path, 1)
    If Not ts.AtEndOfStream Then body = ts.ReadAll
    ts.Close
    parts = Split(Replace(body, vbCr, ""), vbLf)
    hi = UBound(parts)
    Do While hi >= 0 And Trim(parts(hi)) = ""
      hi = hi - 1
    Loop
    lo = hi - maxLines + 1
    If lo < 0 Then lo = 0
    For i = lo To hi
      out = out & parts(i) & vbCrLf
    Next
  End If
  LastLinesOf = out
End Function
