' Run Fun Time from a branch worktree, so an agent's unlanded work can be seen
' on the real screen before it goes into a pull request. Double-click it, pick a
' branch, and that branch's code runs instead of the installed one.
'
' fun_time/branch_session.py is the whole design, including why a branch session
' REPLACES the live one instead of running beside it -- start one while Fun Time
' is open and Fun Time's own "already running" message turns it away.

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' The venv pin launch.vbs makes, for the same reason: fun_time imports its
' sibling packages -- app_support, player_core -- and those are editable installs
' that exist only in .venv. A python taken from PATH dies while importing, before
' any logging is configured, so the launch never happens and never says why.
pythonExe = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")
If Not fso.FileExists(pythonExe) Then
  MsgBox "Fun Time's virtual environment is missing:" & vbCrLf & pythonExe, vbCritical, "Fun Time"
  WScript.Quit 1
End If

primaryState = fso.BuildPath(scriptDir, "state")
If Not fso.FolderExists(primaryState) Then fso.CreateFolder primaryState
listFile = fso.BuildPath(primaryState, "branch_worktrees.txt")
listLog = fso.BuildPath(primaryState, "branch_worktrees.log")
If fso.FileExists(listFile) Then fso.DeleteFile listFile

' Ask the primary checkout what worktrees there are. Through a file rather than
' the process's output: Exec would flash a console window, and only a file can
' carry UTF-16, which is what keeps an em dash in a commit subject readable.
shell.Run "cmd /c cd /d """ & scriptDir & """ && """ & pythonExe & _
          """ -m fun_time.branch_session --list """ & listFile & """ > """ & _
          listLog & """ 2>&1", 0, True

If Not fso.FileExists(listFile) Then
  msg = "Could not list the branch worktrees." & vbCrLf & vbCrLf & _
        "See the full log at:" & vbCrLf & listLog
  tail = LastLinesOf(listLog, 15)
  If Len(tail) > 0 Then msg = msg & vbCrLf & vbCrLf & "Last lines of the log:" & vbCrLf & tail
  MsgBox msg, vbCritical, "Fun Time"
  WScript.Quit 1
End If

Dim paths(), labels()
ReDim paths(31)
ReDim labels(31)
count = 0
Set listStream = fso.OpenTextFile(listFile, 1, False, -1)
Do While Not listStream.AtEndOfStream
  entry = listStream.ReadLine
  If InStr(entry, vbTab) > 0 Then
    If count > UBound(paths) Then
      ReDim Preserve paths(count)
      ReDim Preserve labels(count)
    End If
    paths(count) = Left(entry, InStr(entry, vbTab) - 1)
    labels(count) = Mid(entry, InStr(entry, vbTab) + 1)
    count = count + 1
  End If
Loop
listStream.Close

If count = 0 Then
  MsgBox "There are no branch worktrees to run." & vbCrLf & vbCrLf & _
         "An agent working on a branch makes one under .claude\worktrees, and " & _
         "this lists whatever ""git worktree list"" reports.", vbInformation, "Fun Time"
  WScript.Quit 0
End If

' Only the newest few are listed: InputBox truncates a prompt past roughly a
' thousand characters, and a busy repo carries dozens of worktrees.  Everything
' older is still reachable by typing part of its branch name, which is also the
' shape an agent hands the user ("verify claude/some-branch").
maxShown = 10
shown = count
If shown > maxShown Then shown = maxShown

prompt = "Run Fun Time on which branch?" & vbCrLf & vbCrLf
For i = 0 To shown - 1
  prompt = prompt & (i + 1) & ". " & labels(i) & vbCrLf
Next
If count > shown Then prompt = prompt & vbCrLf & "(" & (count - shown) & " older ones not shown.)" & vbCrLf
prompt = prompt & vbCrLf & _
         "Its code runs instead of the installed one, on your real library." & vbCrLf & _
         "Quit with Ctrl+Alt+Q, then launch Fun Time normally again." & vbCrLf & vbCrLf & _
         "A number, or part of a branch name:"

choice = InputBox(prompt, "Fun Time - verify a branch", "1")
' Cancel and an emptied box both come back empty, and both mean "never mind".
If Len(Trim(choice)) = 0 Then WScript.Quit 0

picked = -1
If IsNumeric(choice) Then
  wanted = CLng(choice)
  If wanted < 1 Or wanted > shown Then
    MsgBox "There is no branch " & wanted & " in the list. Type part of a branch " & _
           "name to reach an older one.", vbCritical, "Fun Time"
    WScript.Quit 1
  End If
  picked = wanted - 1
Else
  needle = LCase(Trim(choice))
  matches = 0
  detail = ""
  For i = 0 To count - 1
    If InStr(LCase(labels(i)), needle) > 0 Or InStr(LCase(paths(i)), needle) > 0 Then
      picked = i
      matches = matches + 1
      If matches <= 10 Then detail = detail & vbCrLf & "  " & labels(i)
    End If
  Next
  If matches = 0 Then
    MsgBox "No worktree matches """ & choice & """.", vbCritical, "Fun Time"
    WScript.Quit 1
  End If
  If matches > 1 Then
    MsgBox matches & " worktrees match """ & choice & """:" & vbCrLf & detail & vbCrLf & _
           vbCrLf & "Type more of the name.", vbCritical, "Fun Time"
    WScript.Quit 1
  End If
End If
worktree = paths(picked)

' Every sentinel this launch is judged by lives in the WORKTREE's state dir. The
' names are launch.vbs's, kept apart by directory instead of by name -- which is
' the same thing that keeps the branch session's command files, playlists and
' logs out of the live session's state.
stateDir = fso.BuildPath(worktree, "state")
If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir
launchLog = fso.BuildPath(stateDir, "launcher.log")
readyFile = fso.BuildPath(stateDir, "launcher.ready")
exitedFlag = fso.BuildPath(stateDir, "launcher.exited")
If fso.FileExists(readyFile) Then fso.DeleteFile readyFile
If fso.FileExists(exitedFlag) Then fso.DeleteFile exitedFlag

' Run from the primary: this launcher and the config it writes are main's code,
' and only the session underneath it is the branch's (branch_session starts the
' orchestrator with its working directory in the worktree).
cmd = "cmd /c cd /d """ & scriptDir & """ && """ & pythonExe & _
      """ -m fun_time.branch_session """ & worktree & """ > """ & launchLog & _
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
  msg = "Fun Time failed to start on " & labels(picked) & "." & vbCrLf & vbCrLf & _
        "See the full log at:" & vbCrLf & launchLog
  tail = LastLinesOf(launchLog, 15)
  If Len(tail) > 0 Then msg = msg & vbCrLf & vbCrLf & "Last lines of the log:" & vbCrLf & tail
  MsgBox msg, vbCritical, "Fun Time"
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
