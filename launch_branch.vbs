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
  launchLog = LaunchLogIn(stateDir, "vr_launcher")
  readyFile = fso.BuildPath(stateDir, "vr_launcher.ready")
  exitedFlag = fso.BuildPath(stateDir, "vr_launcher.exited")
Else
  launchLog = LaunchLogIn(stateDir, "launcher")
  readyFile = fso.BuildPath(stateDir, "launcher.ready")
  exitedFlag = fso.BuildPath(stateDir, "launcher.exited")
End If
' branch_session leaves this when a launch fails on a worktree older than
' the Fun Time he runs -- the usual reason a launcher that worked once
' stops working, and a failure that is nothing he did. Cleared first so a
' previous launch's note cannot explain this one.
outOfDateNote = fso.BuildPath(stateDir, "branch_out_of_date.txt")
If fso.FileExists(readyFile) Then fso.DeleteFile readyFile
If fso.FileExists(exitedFlag) Then fso.DeleteFile exitedFlag
If fso.FileExists(outOfDateNote) Then fso.DeleteFile outOfDateNote

' Run from the primary: this launcher and the config it writes are main's code,
' and only the session underneath it is the branch's (branch_session starts the
' orchestrator with its working directory in the worktree).
cmd = "cmd /c cd /d """ & scriptDir & """ && """ & pythonExe & _
      """ -m fun_time.branch_session """ & worktree & """" & sessionFlag & " >> """ & launchLog & _
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
  outOfDate = LastLinesOf(outOfDateNote, 20)
  If Len(outOfDate) > 0 Then
    MsgBox outOfDate & vbCrLf & "The full log is at:" & vbCrLf & launchLog, _
           vbExclamation, appName
  Else
    msg = appName & " failed to start on " & branchLabel & "." & vbCrLf & vbCrLf & _
          "See the full log at:" & vbCrLf & launchLog
    tail = LastLinesOf(launchLog, 15)
    If Len(tail) > 0 Then msg = msg & vbCrLf & vbCrLf & "Last lines of the log:" & vbCrLf & tail
    MsgBox msg, vbCritical, appName
  End If
End If

' The first of <name>.log, <name>-2.log ... that opens for writing, and a banner
' in it naming this launch.
'
' The redirect below is cmd's, and cmd holds the file it redirects into for as
' long as the session runs -- Windows lets nobody else write it meanwhile. So
' does every child of that session launched without a redirect of its own,
' because an unset stdout is an INHERITED one: Chrome, the broker tray, an
' Origenerator kept across a crossing. While one of those outlives its session
' the file stays held, and a launch redirecting into it fails INSIDE cmd, before
' python is run at all -- no window, no log line anywhere, not even the "already
' running" refusal, which only the interpreter that never started could show. The
' click does nothing, and the app comes up only once the stray has gone: the
' launch that takes two clicks. So take the next free name rather than not
' launch, and leave the held one to whatever is still writing into it.
'
' Appended to rather than overwritten, and banner-stamped, because the retry
' used to erase the failed launch's traceback -- the one record of why the first
' click did nothing. Rolled aside at a megabyte, as the app's own logs are.
Function LaunchLogIn(dirPath, stem)
  Dim i, candidate, ts
  For i = 1 To 9
    If i = 1 Then
      candidate = fso.BuildPath(dirPath, stem & ".log")
    Else
      candidate = fso.BuildPath(dirPath, stem & "-" & i & ".log")
    End If
    RollIfOversize candidate
    On Error Resume Next
    Set ts = fso.OpenTextFile(candidate, 8, True)
    If Err.Number = 0 Then
      Err.Clear
      On Error GoTo 0
      ts.WriteLine "===== " & Now & " launch"
      ts.Close
      LaunchLogIn = candidate
      Exit Function
    End If
    Err.Clear
    On Error GoTo 0
  Next
  LaunchLogIn = fso.BuildPath(dirPath, stem & ".log")
End Function

' Move a log past a megabyte aside to <name>.1, keeping one generation.
' Best-effort: a stray child still holding it makes Windows refuse the rename,
' and housekeeping must never cost a launch.
Sub RollIfOversize(path)
  On Error Resume Next
  If fso.FileExists(path) Then
    If fso.GetFile(path).Size > 1000000 Then
      If fso.FileExists(path & ".1") Then fso.DeleteFile path & ".1"
      fso.MoveFile path, path & ".1"
    End If
  End If
  Err.Clear
  On Error GoTo 0
End Sub

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
