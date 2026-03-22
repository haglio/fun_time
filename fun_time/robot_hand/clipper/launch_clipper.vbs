Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(scriptDir)))
launcherLog = projectRoot & "\state\clipper_launcher.log"

Function Quote(s)
  Quote = Chr(34) & s & Chr(34)
End Function

Sub AppendLog(msg)
  On Error Resume Next
  Dim ts
  Set ts = fso.OpenTextFile(launcherLog, 8, True)
  ts.WriteLine Now & " " & msg
  ts.Close
End Sub

Function FindPythonCommand()
  Dim venvPython, candidates, i
  venvPython = projectRoot & "\.venv\Scripts\python.exe"
  If fso.FileExists(venvPython) Then
    FindPythonCommand = Quote(venvPython)
    Exit Function
  End If

  candidates = Array( _
    "python", _
    "py -3" _
  )
  For i = 0 To UBound(candidates)
    If shell.Run("cmd /c where " & Split(candidates(i), " ")(0) & " >nul 2>nul", 0, True) = 0 Then
      FindPythonCommand = candidates(i)
      Exit Function
    End If
  Next
  FindPythonCommand = ""
End Function

pythonCmd = FindPythonCommand()
If pythonCmd = "" Then
  AppendLog "ERROR: Could not find python launcher"
  MsgBox "Could not find python or py launcher.", vbCritical, "Clipper"
  WScript.Quit 1
End If

cmd = "cmd /c cd /d " & Quote(projectRoot) & " && " & pythonCmd & " -m fun_time.robot_hand.clipper 1>>" & Quote(launcherLog) & " 2>&1"
AppendLog "INFO: Launching with command: " & cmd
shell.Run cmd, 0, False