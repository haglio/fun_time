Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Fun Time runs on the project venv, never on whatever python happens to be on
' PATH. fun_time imports its sibling packages -- app_support, player_core --
' and those are editable installs that exist only in .venv; a PATH python finds
' the sibling *repo* directories as namespace packages instead and dies on
' "No module named 'player_core.playlist'" while importing, before the
' orchestrator has configured any logging. That is a launch that never happens
' and never says why.
pythonExe = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")
If Not fso.FileExists(pythonExe) Then
  MsgBox "Fun Time's virtual environment is missing:" & vbCrLf & pythonExe, vbCritical, "Fun Time"
  WScript.Quit 1
End If

' Run the orchestrator through a copy of that interpreter that says what it is.
' Windows identifies a process by its image name and by the description in its
' version resource, and a plain python.exe supplies "python.exe" and "Python" --
' so every child of a session arrives as one more anonymous Python among
' whatever else the user is running. When a session strands one (an orchestrator
' that dies without reaping leaves its companions alive, with no window left to
' close), the task list is the only way back and it cannot say which rows are
' safe to end. fun_time.process_identity makes these copies and explains the
' mechanism; the orchestrator is the one process it cannot name on the way in,
' because writing the copy takes the very interpreter being launched.
'
' So the naming happens one launch behind: this picks the copy up when it is
' there, the session makes it for the session after, and a checkout that has
' never run starts anonymous exactly as it used to.
namedExe = fso.BuildPath(scriptDir, ".venv\Scripts\FunTime-Orchestrator.exe")
If fso.FileExists(namedExe) Then pythonExe = namedExe

' Everything the orchestrator writes to its console goes here. The launcher runs
' it in a hidden window, and a failure during import happens before any log file
' exists, so without this a crashed launch leaves nothing behind at all.
' Overwritten each launch: it holds this launch's crash, not a history.
stateDir = fso.BuildPath(scriptDir, "state")
If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir
launchLog = fso.BuildPath(stateDir, "launcher.log")

' Two sentinels let the hidden launch report its own outcome. The orchestrator
' writes readyFile once it has passed config validation and is committing to run
' (or once it has shown its own "already running" message), so a silent
' import/config crash -- the whole reason the window can vanish without a word --
' leaves it absent. cmd writes exitedFlag the instant the child exits, however it
' exits, so a crash is caught at once instead of only when the timeout expires.
' Clear both from a prior launch first: their presence must describe THIS launch.
readyFile = fso.BuildPath(stateDir, "launcher.ready")
exitedFlag = fso.BuildPath(stateDir, "launcher.exited")
If fso.FileExists(readyFile) Then fso.DeleteFile readyFile
If fso.FileExists(exitedFlag) Then fso.DeleteFile exitedFlag

cmd = "cmd /c cd /d """ & scriptDir & """ && """ & pythonExe & """ -m fun_time.orchestrator > """ & launchLog & """ 2>&1 & type nul > """ & exitedFlag & """"
shell.Run cmd, 0, False

' Watch the sentinels. A good launch drops readyFile within a second or two; a
' crash trips exitedFlag first; a launch wedged before it can do either trips the
' timeout. Every failure path pops the log so the user sees why the window never
' appeared instead of staring at nothing.
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
  msg = "Fun Time failed to start." & vbCrLf & vbCrLf & _
        "See the full log at:" & vbCrLf & launchLog
  tail = LastLinesOf(launchLog, 15)
  If Len(tail) > 0 Then msg = msg & vbCrLf & vbCrLf & "Last lines of the log:" & vbCrLf & tail
  MsgBox msg, vbCritical, "Fun Time"
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
