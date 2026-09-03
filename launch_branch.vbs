' Runs Fun Time from a branch worktree, so an agent's unlanded work can be seen
' on the real screen before it goes into a pull request.
'
' Not double-clicked directly: an agent with a branch to show makes a
' "Verify <branch>.lnk" beside this file, and that shortcut passes the worktree
' in. fun_time/branch_session.py is the whole design, including why a branch
' session REPLACES the live one instead of running beside it -- start one while
' Fun Time is open and Fun Time's own "already running" message turns it away.

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

If WScript.Arguments.Count < 1 Then
  MsgBox "There is nothing to run here on its own." & vbCrLf & vbCrLf & _
         "An agent with a branch for you to look at leaves a ""Verify <branch>"" " & _
         "shortcut in this folder, and names it for you. Double-click that instead.", _
         vbInformation, "Fun Time"
  WScript.Quit 1
End If

worktree = WScript.Arguments(0)

' Which session this shortcut is for. The VR flavour passes "--vr" after the
' branch, so one launcher serves both and there is one sweep, one stale check
' and one failure dialog to keep right instead of two.
isVR = False
For argIndex = 1 To WScript.Arguments.Count - 1
  If LCase(WScript.Arguments(argIndex)) = "--vr" Then isVR = True
Next

If WScript.Arguments.Count > 1 And LCase(WScript.Arguments(1)) <> "--vr" Then
  branchLabel = WScript.Arguments(1)
Else
  branchLabel = worktree
End If

If isVR Then
  appName = "Fun Time VR"
  sessionFlag = " --vr"
Else
  appName = "Fun Time"
  sessionFlag = ""
End If

If Not fso.FolderExists(worktree) Then
  MsgBox "That branch's worktree is gone:" & vbCrLf & worktree & vbCrLf & vbCrLf & _
         "It was probably deleted when the branch landed, in which case the work " & _
         "is already in Fun Time. This shortcut can be deleted.", vbCritical, appName
  WScript.Quit 1
End If

' The venv pin launch.vbs makes, for the same reason: fun_time imports its
' sibling packages -- app_support, player_core -- and those are editable installs
' that exist only in .venv. A python taken from PATH dies while importing, before
' any logging is configured, so the launch never happens and never says why. It
' is the PRIMARY checkout's venv: a worktree has none of its own.
pythonExe = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")
If Not fso.FileExists(pythonExe) Then
  MsgBox appName & "'s virtual environment is missing:" & vbCrLf & pythonExe, vbCritical, appName
  WScript.Quit 1
End If

' Every sentinel this launch is judged by lives in the WORKTREE's state dir. The
' names are launch.vbs's, kept apart by directory instead of by name -- which is
' the same thing that keeps the branch session's command files, playlists and
' logs out of the live session's state.
stateDir = fso.BuildPath(worktree, "state")
If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir
' FunTimeVR's orchestrator drops vr_launcher.ready, not the desktop marker, so
' a VR launch watched for the wrong file would pop "failed to start" over a
' session that had come up perfectly well.  Spelled out either side rather than
' built from a stem, so every name a launcher answers to can still be grepped.
If isVR Then
  launchLog = fso.BuildPath(stateDir, "vr_launcher.log")
  readyFile = fso.BuildPath(stateDir, "vr_launcher.ready")
  exitedFlag = fso.BuildPath(stateDir, "vr_launcher.exited")
Else
  launchLog = fso.BuildPath(stateDir, "launcher.log")
  readyFile = fso.BuildPath(stateDir, "launcher.ready")
  exitedFlag = fso.BuildPath(stateDir, "launcher.exited")
End If
If fso.FileExists(readyFile) Then fso.DeleteFile readyFile
If fso.FileExists(exitedFlag) Then fso.DeleteFile exitedFlag

' Run from the primary: this launcher and the config it writes are main's code,
' and only the session underneath it is the branch's (branch_session starts the
' orchestrator with its working directory in the worktree).
cmd = "cmd /c cd /d """ & scriptDir & """ && """ & pythonExe & _
      """ -m fun_time.branch_session """ & worktree & """" & sessionFlag & " > """ & launchLog & _
      """ 2>&1 & type nul > """ & exitedFlag & """"
shell.Run cmd, 0, False

' Watch the sentinels, exactly as launch.vbs does. A good launch drops readyFile
' within a second or two; a crash trips exitedFlag first; a launch wedged before
' it can do either trips the timeout.
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
  msg = appName & " failed to start on " & branchLabel & "." & vbCrLf & vbCrLf & _
        "See the full log at:" & vbCrLf & launchLog
  tail = LastLinesOf(launchLog, 15)
  If Len(tail) > 0 Then msg = msg & vbCrLf & vbCrLf & "Last lines of the log:" & vbCrLf & tail
  MsgBox msg, vbCritical, appName
End If

' Return the tail of a text file (up to maxLines non-blank-terminated lines), so
' a failure dialog can show what landed in the log without making the user go
' open it.
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
