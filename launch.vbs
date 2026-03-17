Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Function FindPythonCommand()
  Dim candidates, i
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
  MsgBox "Could not find python or py launcher.", vbCritical, "Fun Time"
  WScript.Quit 1
End If

cmd = "cmd /c cd /d """ & scriptDir & """ && " & pythonCmd & " -m fun_time.orchestrator"

shell.Run cmd, 0, False