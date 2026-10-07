param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install')]
    [string]$Mode,
    [string]$ProjectDir,
    [string]$TaskName = 'MT5DashboardMonitor',
    [string]$Url = 'http://127.0.0.1:8765'
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This script requires Windows.' }
$scriptPath = $MyInvocation.MyCommand.Path
if (-not $scriptPath) { $scriptPath = $PSCommandPath }
if (-not $scriptPath) { throw 'Cannot determine script path; run with -File.' }
if (-not $ProjectDir) { $ProjectDir = Split-Path -Parent (Split-Path -Parent $scriptPath) }
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
$configPath = Join-Path $ProjectDir '.dashboard-start.local.json'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Startup settings are missing: $configPath. Save the existing dashboard task settings first."
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.version -ne 1) { throw 'Unsupported startup settings version' }
if ($config.user_sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
    throw 'Startup settings were encrypted for a different Windows user'
}
if (-not (Test-Path -LiteralPath ([string]$config.python) -PathType Leaf)) {
    throw 'Saved Python executable is missing'
}
if ($Url.Contains('"')) { throw 'URL must not contain a quote' }

$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$pythonw = Join-Path (Split-Path -Parent ([string]$config.python)) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "Python windowless executable is missing: $pythonw"
}
$arguments = '-m server.uptime_monitor --url "' + $Url + '" ' +
             '--state ".dashboard-monitor-state.json" ' +
             '--saved-settings ".dashboard-start.local.json" ' +
             '--log "run-logs\monitor.log"'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description 'Independent MT5 dashboard availability monitor' -Force | Out-Null
Write-Host "Installed independent task $TaskName for $user"
