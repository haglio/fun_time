Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $projectRoot 'state'
$brokerLog = Join-Path $stateDir 'broker.log'
$modeFile = Join-Path $stateDir 'robot_hand_mode.txt'
$runnerScript = Join-Path $PSScriptRoot 'run_broker_service.ps1'

function Get-BrokerProcess {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^pythonw?\.exe$|^py\.exe$' -and $_.CommandLine -match 'fun_time\.broker_app'
    } | Select-Object -First 1
}

function Start-BrokerProcess {
    if (Get-BrokerProcess) {
        return
    }

    Start-Process -FilePath 'powershell.exe' `
        -WindowStyle Hidden `
        -ArgumentList @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', $runnerScript
        ) | Out-Null
}

function Restart-BrokerProcess {
    $proc = Get-BrokerProcess
    if ($proc) {
        Stop-Process -Id $proc.ProcessId -Force
        Start-Sleep -Milliseconds 300
    }
    Start-BrokerProcess
}

function Get-ModeText {
    if (-not (Test-Path $modeFile)) {
        return 'unknown'
    }

    try {
        $mode = (Get-Content -Path $modeFile -Raw).Trim()
    } catch {
        return 'unknown'
    }

    switch ($mode) {
        '0' { return 'control' }
        '1' { return 'auto' }
        '2' { return 'stale-timeout' }
        default { return "mode=$mode" }
    }
}

function Update-NotifyIcon {
    $proc = Get-BrokerProcess
    $modeText = Get-ModeText

    if ($proc) {
        $script:notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
        $script:notifyIcon.Text = "Fun Time Broker: running ($modeText)"
    } else {
        $script:notifyIcon.Icon = [System.Drawing.SystemIcons]::Error
        $script:notifyIcon.Text = 'Fun Time Broker: stopped'
    }
}

[System.Windows.Forms.Application]::EnableVisualStyles()

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Visible = $true
$notifyIcon.Text = 'Fun Time Broker'

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$statusItem = $menu.Items.Add('Status refresh')
$statusItem.add_Click({
    Update-NotifyIcon
})

$startItem = $menu.Items.Add('Start broker')
$startItem.add_Click({
    Start-BrokerProcess
    Start-Sleep -Milliseconds 500
    Update-NotifyIcon
})

$restartItem = $menu.Items.Add('Restart broker')
$restartItem.add_Click({
    Restart-BrokerProcess
    Start-Sleep -Milliseconds 500
    Update-NotifyIcon
})

$logItem = $menu.Items.Add('Open broker log')
$logItem.add_Click({
    if (-not (Test-Path $brokerLog)) {
        New-Item -ItemType File -Path $brokerLog -Force | Out-Null
    }
    Start-Process notepad.exe $brokerLog
})

$menu.Items.Add('-') | Out-Null

$exitItem = $menu.Items.Add('Exit tray')
$exitItem.add_Click({
    $script:timer.Stop()
    $script:notifyIcon.Visible = $false
    $script:notifyIcon.Dispose()
    $script:timer.Dispose()
    [System.Windows.Forms.Application]::Exit()
})

$notifyIcon.ContextMenuStrip = $menu
$notifyIcon.add_DoubleClick({
    Update-NotifyIcon
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({
    Update-NotifyIcon
})

$script:notifyIcon = $notifyIcon
$script:timer = $timer

Start-BrokerProcess
Update-NotifyIcon
$timer.Start()
[System.Windows.Forms.Application]::Run()
