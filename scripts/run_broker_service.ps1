Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$configPath = Join-Path $projectRoot 'fun_time_config.json'
if (-not (Test-Path $configPath)) {
    throw "Config not found: $configPath"
}

$config = Get-Content -Path $configPath -Raw | ConvertFrom-Json
$pythonExe = [string]$config.paths.python_exe

if ([string]::IsNullOrWhiteSpace($pythonExe)) {
    $pythonExe = 'python'
}

if (-not [System.IO.Path]::IsPathRooted($pythonExe)) {
    $pythonExe = (Join-Path $projectRoot $pythonExe)
}

if ([System.IO.Path]::GetFileName($pythonExe).ToLowerInvariant() -eq 'pythonw.exe') {
    $pythonConsoleExe = Join-Path ([System.IO.Path]::GetDirectoryName($pythonExe)) 'python.exe'
    if (Test-Path $pythonConsoleExe) {
        $pythonExe = $pythonConsoleExe
    }
}

$launcherLog = Join-Path $projectRoot 'state\broker_service_launcher.log'
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'state') -Force | Out-Null

if (-not (Test-Path $pythonExe)) {
    "$(Get-Date -Format s) WARN Config python_exe not found: $pythonExe. Falling back to py -3." | Add-Content -Path $launcherLog -Encoding UTF8
    & py -3 -m fun_time.broker_app --config $configPath 1>> $launcherLog 2>&1
    exit $LASTEXITCODE
}

"$(Get-Date -Format s) INFO Starting broker with $pythonExe" | Add-Content -Path $launcherLog -Encoding UTF8
& $pythonExe -m fun_time.broker_app --config $configPath 1>> $launcherLog 2>&1
if (Get-Variable LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue) {
    exit $global:LASTEXITCODE
}
exit 0
