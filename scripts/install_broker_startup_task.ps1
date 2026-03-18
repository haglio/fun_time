Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $PSScriptRoot 'run_broker_service.ps1'
$taskName = 'FunTime Robot Hand Broker'

if (-not (Test-Path $runnerPath)) {
    throw "Runner script not found: $runnerPath"
}

$pwsh = Join-Path $PSHOME 'powershell.exe'
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runnerPath`""

$action = New-ScheduledTaskAction -Execute $pwsh -Argument $actionArgs -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$userId = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Installed scheduled task: $taskName"
    Write-Host "It will start the broker when you sign in to Windows."
    Write-Host "To start it now: Start-ScheduledTask -TaskName '$taskName'"
    Write-Host "To remove it: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
}
catch {
    $startupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    New-Item -ItemType Directory -Path $startupDir -Force | Out-Null

    $startupVbs = Join-Path $startupDir 'FunTime Robot Hand Broker.vbs'
    $runCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$runnerPath"""
    $vbsRun = 'shell.Run "' + $runCmd + '", 0, False'
    $vbs = @(
        'Set shell = CreateObject("WScript.Shell")'
        $vbsRun
    ) -join "`r`n"

    Set-Content -Path $startupVbs -Value $vbs -Encoding ASCII

    Write-Warning "Scheduled Task installation failed (likely permissions)."
    Write-Host "Installed Startup-folder launcher instead: $startupVbs"
    Write-Host "It will start the broker when you sign in to Windows."
    Write-Host "To remove it: Remove-Item '$startupVbs'"
}
