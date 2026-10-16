' Rendered from [tool.haglio.launchers."launch_main_verifier.vbs"] in pyproject.toml.
' Change the spec, then run  python -m app_support.launcher --write  in this
' folder: the suite fails on a launcher that differs from its spec.

Option Explicit

Dim fso, shell, root, app, interpreter, directory, arguments, base, updater, gates, held()

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
  app = "Fun Time main verifier"
  arguments = "-m tests.integration.main_verifier"
  interpreter = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
  directory = root
  base = root
  updater = fso.BuildPath(fso.GetParentFolderName(base), "app_support\.venv\Scripts\pythonw.exe")
  gates = Array(fso.BuildPath(base, ".git\production-update.turn"), fso.BuildPath(fso.GetParentFolderName(base), "origenerator\.git\production-update.turn"))
End Sub

Sub Report()
  WScript.Echo "app: " & app
  WScript.Echo "interpreter: " & interpreter
  WScript.Echo "directory: " & directory
  WScript.Echo "arguments: " & arguments
  WScript.Echo "environment: " & "HAGLIO_UPKEEP" & "=" & "1"
  WScript.Echo "command: " & Command()
  WScript.Echo "update: " & Update()
  Dim gate
  For Each gate In gates
    WScript.Echo "gate: " & gate
  Next
End Sub

Sub Launch()
  If Not fso.FileExists(interpreter) Then
    Refuse app & "'s virtual environment is missing:" & vbCrLf & interpreter, vbCritical
  End If
  CatchUp
  HoldGates
  shell.Environment("Process").Item("HAGLIO_UPKEEP") = "1"
  shell.CurrentDirectory = directory
  shell.Run Command(), 0, False
  ReleaseGates
End Sub

Function Command()
  Command = Quote(interpreter) & " " & arguments
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

Function Update()
  Update = Quote(updater) & " -m app_support.production_update --before-launch " & Quote(base)
End Function

Sub CatchUp()
  If fso.FileExists(updater) Then shell.Run Update(), 0, True
End Sub

Sub HoldGates()
  Dim index, waited
  ReDim held(UBound(gates))
  For index = 0 To UBound(gates)
    If fso.FolderExists(fso.GetParentFolderName(gates(index))) Then
      waited = 0
      Do Until Holds(index)
        If waited >= 900000 Then WScript.Quit 1
        WScript.Sleep 500
        waited = waited + 500
      Loop
    End If
  Next
End Sub

Function Holds(index)
  On Error Resume Next
  Set held(index) = fso.OpenTextFile(gates(index), 1, True)
  Holds = (Err.Number = 0)
  Err.Clear
  On Error GoTo 0
End Function

Sub ReleaseGates()
  Dim index
  For index = 0 To UBound(held)
    If IsObject(held(index)) Then held(index).Close
  Next
End Sub
