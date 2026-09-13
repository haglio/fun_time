' Rendered from [tool.haglio.launchers."launch.vbs"] in pyproject.toml.
' Change the spec, then run  python -m app_support.launcher --write  in this
' folder: the suite fails on a launcher that differs from its spec.

Option Explicit

Dim fso, shell, root, app, interpreter, directory, arguments, logPath, readyFile, exitedFlag

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Decide
If shell.Environment("Process").Item("HAGLIO_LAUNCHER_DRY_RUN") = "1" Then
  Report
Else
  Launch
End If

Sub Decide()
  app = "Fun Time"
  logPath = fso.BuildPath(root, "state\launcher.log")
  readyFile = fso.BuildPath(root, "state\launcher.ready")
  exitedFlag = fso.BuildPath(root, "state\launcher.exited")
  arguments = "-m fun_time.orchestrator"
  interpreter = fso.BuildPath(root, ".venv\Scripts\python.exe")
  If fso.FileExists(fso.BuildPath(root, ".venv\Scripts\FunTime-Orchestrator.exe")) Then interpreter = fso.BuildPath(root, ".venv\Scripts\FunTime-Orchestrator.exe")
  directory = root
End Sub

Sub Report()
  WScript.Echo "app: " & app
  WScript.Echo "interpreter: " & interpreter
  WScript.Echo "directory: " & directory
  WScript.Echo "arguments: " & arguments
  WScript.Echo "log: " & logPath
  WScript.Echo "ready: " & readyFile
  WScript.Echo "exited: " & exitedFlag
  WScript.Echo "command: " & Command()
End Sub

Sub Launch()
  If Not fso.FileExists(interpreter) Then
    Refuse app & "'s virtual environment is missing:" & vbCrLf & interpreter, vbCritical
  End If
  logPath = FreeLog(logPath)
  Note logPath, "===== " & Now & " launch: " & Command()
  If fso.FileExists(readyFile) Then fso.DeleteFile readyFile
  If fso.FileExists(exitedFlag) Then fso.DeleteFile exitedFlag
  shell.Run Command(), 0, False
  If Not Started() Then
    Refuse FailedStart(), vbCritical
  End If
End Sub

Function Command()
  Command = "cmd /c cd /d " & Quote(directory) & " && " & Quote(interpreter) & " " & arguments & " >> " & Quote(logPath) & " 2>&1 & type nul > " & Quote(exitedFlag)
End Function

Function Quote(text)
  Quote = Chr(34) & text & Chr(34)
End Function

Sub Tell(message, icon)
  If LCase(fso.GetFileName(WScript.FullName)) = "cscript.exe" Then
    WScript.Echo "dialog: " & message
  Else
    MsgBox message, icon, app
  End If
End Sub

Sub Refuse(message, icon)
  Tell message, icon
  WScript.Quit 1
End Sub

Function FreeLog(preferred)
  Dim folder, candidate, index
  folder = fso.GetParentFolderName(preferred)
  If Not fso.FolderExists(folder) Then fso.CreateFolder folder
  For index = 1 To 9
    candidate = preferred
    If index > 1 Then
      candidate = fso.BuildPath(folder, fso.GetBaseName(preferred) & "-" & index & "." & fso.GetExtensionName(preferred))
    End If
    RollIfOversize candidate
    If CanAppend(candidate) Then
      FreeLog = candidate
      Exit Function
    End If
  Next
  FreeLog = preferred
End Function

Function CanAppend(path)
  Dim stream
  On Error Resume Next
  Set stream = fso.OpenTextFile(path, 8, True)
  CanAppend = (Err.Number = 0)
  If CanAppend Then stream.Close
  Err.Clear
  On Error GoTo 0
End Function

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

Sub Note(path, line)
  Dim stream
  On Error Resume Next
  Set stream = fso.OpenTextFile(path, 8, True)
  stream.WriteLine line
  stream.Close
  Err.Clear
  On Error GoTo 0
End Sub

Function Started()
  Dim waited
  waited = 0
  Started = False
  Do
    If fso.FileExists(readyFile) Then
      Started = True
      Exit Function
    End If
    If fso.FileExists(exitedFlag) Or waited >= 45000 Then Exit Function
    WScript.Sleep 250
    waited = waited + 250
  Loop
End Function

Function FailedStart()
  Dim tail
  FailedStart = app & " failed to start." & vbCrLf & vbCrLf & "See the full log at:" & vbCrLf & logPath
  tail = LastLinesOf(logPath, 15)
  If Len(tail) > 0 Then
    FailedStart = FailedStart & vbCrLf & vbCrLf & "Last lines of the log:" & vbCrLf & tail
  End If
End Function

Function LastLinesOf(path, count)
  Dim stream, body, lines, first, last, index
  LastLinesOf = ""
  If Not fso.FileExists(path) Then Exit Function
  Set stream = fso.OpenTextFile(path, 1)
  body = ""
  If Not stream.AtEndOfStream Then body = stream.ReadAll
  stream.Close
  lines = Split(Replace(body, vbCr, ""), vbLf)
  last = UBound(lines)
  Do While last >= 0
    If Trim(lines(last)) <> "" Then Exit Do
    last = last - 1
  Loop
  first = last - count + 1
  If first < 0 Then first = 0
  For index = first To last
    LastLinesOf = LastLinesOf & lines(index) & vbCrLf
  Next
End Function
