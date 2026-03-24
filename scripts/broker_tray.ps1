Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $projectRoot 'state'
$brokerLog = Join-Path $stateDir 'broker.log'
$modeFile = Join-Path $stateDir 'robot_hand_mode.txt'
$runnerScript = Join-Path $PSScriptRoot 'run_broker_service.ps1'
$trayIconPath = Join-Path $projectRoot 'icon.ico'

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

function Stop-BrokerProcess {
    $proc = Get-BrokerProcess
    if ($proc) {
        Stop-Process -Id $proc.ProcessId -Force
    }
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

function Get-BrokerStatus {
    $proc = Get-BrokerProcess
    return [pscustomobject]@{
        IsRunning = ($null -ne $proc)
        ModeText = Get-ModeText
    }
}

function Update-NotifyIcon {
    $status = Get-BrokerStatus
    $runningText = if ($status.IsRunning) { 'running' } else { 'stopped' }

    if ($script:trayIcon -ne $null) {
        $script:notifyIcon.Icon = $script:trayIcon
    }
    elseif ($status.IsRunning) {
        $script:notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
    }
    else {
        $script:notifyIcon.Icon = [System.Drawing.SystemIcons]::Error
    }

    if ($status.IsRunning) {
        $script:notifyIcon.Text = "Fun Time Broker: running ($($status.ModeText))"
    } else {
        $script:notifyIcon.Text = 'Fun Time Broker: stopped'
    }

    $script:statusItem.Text = "Broker status: $runningText ($($status.ModeText))"
    $script:statusItem.Enabled = $false
    $script:actionItem.Text = if ($status.IsRunning) { 'Restart broker' } else { 'Start broker' }
    $script:actionItem.ToolTipText = if ($status.IsRunning) {
        'Restart the broker service process'
    } else {
        'Start the broker service process'
    }
    $script:pauseItem.Enabled = $status.IsRunning
    $script:pauseItem.Text = 'Pause broker'
}

[System.Windows.Forms.Application]::EnableVisualStyles()

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Visible = $true
$notifyIcon.Text = 'Fun Time Broker'
$trayIcon = $null
if (Test-Path $trayIconPath) {
    $trayIcon = New-Object System.Drawing.Icon($trayIconPath)
    $notifyIcon.Icon = $trayIcon
}

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$statusItem = $menu.Items.Add('Broker status: unknown')
$statusItem.Enabled = $false

$actionItem = $menu.Items.Add('Start broker')
$actionItem.add_Click({
    if ((Get-BrokerStatus).IsRunning) {
        Restart-BrokerProcess
    }
    else {
        Start-BrokerProcess
    }
    Start-Sleep -Milliseconds 500
    Update-NotifyIcon
})

$pauseItem = $menu.Items.Add('Pause broker')
$pauseItem.add_Click({
    Stop-BrokerProcess
    Start-Sleep -Milliseconds 300
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

$quitItem = $menu.Items.Add('Quit')
$quitItem.add_Click({
    Stop-BrokerProcess
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
$script:statusItem = $statusItem
$script:actionItem = $actionItem
$script:pauseItem = $pauseItem
$script:trayIcon = $trayIcon

Start-BrokerProcess
Update-NotifyIcon
$timer.Start()
[System.Windows.Forms.Application]::Run()
