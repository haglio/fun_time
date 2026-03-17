Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Function ToBashPath(winPath)
  Dim drive, rest
  drive = LCase(Left(winPath, 1))
  rest = Mid(winPath, 3)
  rest = Replace(rest, "\", "/")
  ToBashPath = "/" & drive & rest
End Function

Function FindBashExe()
  Dim candidates, i
  candidates = Array( _
    "C:\Program Files\Git\bin\bash.exe", _
    "C:\Program Files\Git\usr\bin\bash.exe", _
    "C:\Program Files (x86)\Git\bin\bash.exe" _
  )
  For i = 0 To UBound(candidates)
    If fso.FileExists(candidates(i)) Then
      FindBashExe = candidates(i)
      Exit Function
    End If
  Next
  FindBashExe = ""
End Function

bashExe = FindBashExe()
If bashExe = "" Then
  MsgBox "Could not find bash.exe", vbCritical, "Fun Time"
  WScript.Quit 1
End If

bashDir = ToBashPath(scriptDir)
cmd = """" & bashExe & """" & " -lc " & Chr(34) & "cd '" & bashDir & "' && bash ./main.sh" & Chr(34)

shell.Run cmd, 0, False