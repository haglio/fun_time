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

' Everything the orchestrator writes to its console goes here. The launcher runs
' it in a hidden window, and a failure during import happens before any log file
' exists, so without this a crashed launch leaves nothing behind at all.
' Overwritten each launch: it holds this launch's crash, not a history.
stateDir = fso.BuildPath(scriptDir, "state")
If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir
launchLog = fso.BuildPath(stateDir, "launcher.log")

cmd = "cmd /c cd /d """ & scriptDir & """ && """ & pythonExe & """ -m fun_time.orchestrator > """ & launchLog & """ 2>&1"

shell.Run cmd, 0, False
