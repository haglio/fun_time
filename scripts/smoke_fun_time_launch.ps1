param(
    [string]$ConfigPath = "fun_time_config.json",
    [int]$WaitSeconds = 35,
    [string]$OutputPath = "state\fun_time_launch_smoke.json"
)

$ErrorActionPreference = "Stop"

function Get-VisibleChromeWindows {
    Get-Process chrome -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle } |
        Select-Object Id, MainWindowTitle
}

function Get-RecentProcessSnapshot {
    Get-Process AutoHotkey64, python, pythonw, vlc, MultiFunPlayer, chrome -ErrorAction SilentlyContinue |
        Select-Object ProcessName, Id, StartTime, MainWindowTitle
}

$projectDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectDir ".venv\Scripts\python.exe"
$windowsBridgeLog = Join-Path $projectDir "state\windows_bridge.log"
$orchestratorLog = Join-Path $projectDir "state\orchestrator.log"
$browserManifest = Join-Path $projectDir "state\random_favs_browser_urls.txt"
$outputFile = Join-Path $projectDir $OutputPath

$beforeChrome = @(Get-VisibleChromeWindows)
$beforeProcesses = @(Get-RecentProcessSnapshot)

$proc = $null
try {
    $proc = Start-Process -FilePath $pythonExe `
        -ArgumentList "-m", "fun_time.orchestrator", "--config", $ConfigPath `
        -WorkingDirectory $projectDir `
        -PassThru

    Start-Sleep -Seconds $WaitSeconds

    $afterChrome = @(Get-VisibleChromeWindows)
    $afterProcesses = @(Get-RecentProcessSnapshot)
    $result = [pscustomobject]@{
        timestamp = (Get-Date).ToString("o")
        orchestrator_pid = if ($proc) { $proc.Id } else { 0 }
        chrome_before = @($beforeChrome | ForEach-Object { [pscustomobject]@{ id = $_.Id; title = $_.MainWindowTitle } })
        chrome_after = @($afterChrome | ForEach-Object { [pscustomobject]@{ id = $_.Id; title = $_.MainWindowTitle } })
        recent_processes_before = @($beforeProcesses)
        recent_processes_after = @($afterProcesses)
        windows_bridge_log_tail = if (Test-Path $windowsBridgeLog) { @(Get-Content $windowsBridgeLog -Tail 80) } else { @("NO_WINDOWS_BRIDGE_LOG") }
        orchestrator_log_tail = if (Test-Path $orchestratorLog) { @(Get-Content $orchestratorLog -Tail 80) } else { @("NO_ORCHESTRATOR_LOG") }
        random_favs_browser_manifest = if (Test-Path $browserManifest) { @(Get-Content $browserManifest) } else { @("NO_BROWSER_MANIFEST") }
    }

    $outputDir = Split-Path -Parent $outputFile
    if ($outputDir) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }
    $result | ConvertTo-Json -Depth 5 | Set-Content -Path $outputFile -Encoding UTF8
    $result | ConvertTo-Json -Depth 5
}
finally {
    if ($proc) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    Get-Process AutoHotkey64, python, pythonw, vlc, MultiFunPlayer, chrome -ErrorAction SilentlyContinue |
        Where-Object { $_.StartTime -gt (Get-Date).AddMinutes(-2) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
