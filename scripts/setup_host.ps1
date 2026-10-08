param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Prepare', 'Install', 'Check')]
    [string]$Mode,
    [ValidateSet('Logs', 'Accounts', 'Full')]
    [string]$Profile = 'Full',
    [string]$ProjectDir,
    [string]$PlanDir = 'host-setup',
    [string]$PythonExe,
    [string]$ServerUrl,
    [string]$HostId = $env:COMPUTERNAME.ToLowerInvariant(),
    [string]$DayTimezone = 'UTC',
    [string[]]$Roots = @(),
    [string[]]$Terminals = @(),
    [switch]$InstallDependencies,
    [switch]$Online,
    [Security.SecureString]$CollectorToken,
    [Security.SecureString]$TelegramBotToken,
    [string]$TelegramChatId
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Use Windows under the interactive MT5 user.' }
if (-not $ProjectDir) { $ProjectDir = Split-Path -Parent $PSScriptRoot }
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
Set-Location -LiteralPath $ProjectDir
$env:PYTHONIOENCODING = 'utf-8'
if (-not [IO.Path]::IsPathRooted($PlanDir)) { $PlanDir = Join-Path $ProjectDir $PlanDir }

function Invoke-Python([string[]]$Arguments) {
    & $script:PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python step failed (exit $LASTEXITCODE). Resolve the reported issue and rerun this mode." }
}
function Assert-NoTask([string]$Name) {
    if (Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq $Name }) {
        throw "Task $Name already exists. This installer is for a new host; review the existing installation instead of replacing it."
    }
}
function Publish-Proposal([string]$Name) {
    $from = Join-Path $PlanDir $Name
    $to = Join-Path $ProjectDir $Name
    if (Test-Path -LiteralPath $to) {
        if ((Get-FileHash -LiteralPath $from).Hash -ne (Get-FileHash -LiteralPath $to).Hash) {
            throw "Existing $Name differs from the reviewed proposal. Review it before an intentional migration."
        }
    } else { Copy-Item -LiteralPath $from -Destination $to }
}

if ($Mode -eq 'Install') {
    $plan = Get-Content -LiteralPath (Join-Path $PlanDir 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($plan.version -ne 1 -or @('Logs', 'Accounts', 'Full') -notcontains $plan.profile) { throw 'Invalid plan version/profile.' }
    $Profile = [string]$plan.profile
    $PythonExe = [string]$plan.python
    $ServerUrl = [string]$plan.server_url
    $HostId = [string]$plan.host_id
    Assert-NoTask 'MT5CollectorRemote'
    if ($Profile -eq 'Full') { Assert-NoTask 'MT5DashboardMonitor' }
    foreach ($name in @('.dashboard-remote.local.json', '.dashboard-monitor.local.json')) {
        if (Test-Path -LiteralPath (Join-Path $ProjectDir $name)) { throw "Existing $name must be reviewed before installation." }
    }
} elseif ($Mode -eq 'Check' -and -not $PythonExe) {
    $settings = Get-Content -LiteralPath '.dashboard-remote.local.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    $PythonExe = [string]$settings.python
}
if (-not $PythonExe) {
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (Test-Path -LiteralPath $candidate) { $PythonExe = $candidate }
    else {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') { $PythonExe = $pythonCommand.Source }
    }
}
if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw 'Python was not found. Install 64-bit Python 3.13 (docs/collector.md) and pass its full path in -PythonExe.'
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
Invoke-Python -Arguments @('-c', "import sys, struct; assert sys.version_info >= (3, 11) and struct.calcsize('P') == 8, '64-bit Python 3.11+ required; use 3.13 for MT5'")

if ($Mode -eq 'Check') {
    $arguments = @('-m', 'collector.diagnose', '--project', $ProjectDir, '--profile', $Profile)
    if ($Online) { $arguments += '--online' }
    & $PythonExe @arguments
    exit $LASTEXITCODE
}
if ($Mode -eq 'Prepare') {
    if (-not $ServerUrl) { throw 'Prepare requires -ServerUrl with the central HTTPS origin.' }
    if ($Profile -ne 'Logs') {
        if ($InstallDependencies) { Invoke-Python -Arguments @('-m', 'pip', 'install', 'MetaTrader5', 'tzdata') }
        Invoke-Python -Arguments @('-c', "import MetaTrader5; from shared.timezones import day_zone; day_zone('Etc/GMT-3')")
    }
    if (-not $Roots) { $Roots = @((Join-Path $env:APPDATA 'MetaQuotes\Terminal')) }
    $arguments = @('-m', 'collector.onboarding', '--output', $PlanDir, '--profile', $Profile,
        '--server-url', $ServerUrl, '--host-id', $HostId, '--day-timezone', $DayTimezone)
    foreach ($path in $Roots) { $arguments += @('--root', $path) }
    foreach ($path in $Terminals) { $arguments += @('--terminal', $path) }
    Invoke-Python $arguments
    Write-Host "Review $PlanDir\plan.json, inventory.json and accounts.json (if present), then use -Mode Install -PlanDir `"$PlanDir`"."
    exit 0
}

# Validate all proposed files and running account identities before publishing them.
Invoke-Python -Arguments @('-m', 'collector.onboarding', '--validate', '--output', $PlanDir)
foreach ($name in @('inventory.json', 'accounts.json')) {
    if ($name -eq 'accounts.json' -and $Profile -eq 'Logs') { continue }
    $existing = Join-Path $ProjectDir $name
    if ((Test-Path -LiteralPath $existing) -and
        (Get-FileHash -LiteralPath $existing).Hash -ne (Get-FileHash -LiteralPath (Join-Path $PlanDir $name)).Hash) {
        throw "Existing $name differs; installer will not overwrite it."
    }
}
if (-not $CollectorToken) { $CollectorToken = Read-Host 'Collector token registered centrally for this host ID' -AsSecureString }
if (-not $CollectorToken -or $CollectorToken.Length -lt 32) { throw 'Collector token must contain at least 32 characters.' }
if ($Profile -eq 'Full') {
    if (-not (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $PythonExe) 'pythonw.exe'))) { throw 'pythonw.exe is required for Full profile.' }
    if (-not $TelegramBotToken) { $TelegramBotToken = Read-Host 'Telegram bot token for availability alerts' -AsSecureString }
    if (-not $TelegramChatId) { $TelegramChatId = Read-Host 'Telegram chat ID' }
    if (-not $TelegramBotToken -or $TelegramBotToken.Length -eq 0 -or -not $TelegramChatId.Trim()) {
        throw 'Full profile requires Telegram bot token and chat ID. Use Accounts if availability alerts are not needed.'
    }
    # Prepare/compile services before registering tasks. Starting them is an explicit MT5 step.
    Invoke-Python -Arguments @('-m', 'collector.install_ea_probe', '--inventory', (Join-Path $PlanDir 'inventory.json'))
}
Publish-Proposal 'inventory.json'
if ($Profile -ne 'Logs') { Publish-Proposal 'accounts.json' }
$scan = @('-m', 'collector', '--db', 'collector-central.db', '--host-id', $HostId,
    '--inventory', 'inventory.json', '--server-url', $ServerUrl)
foreach ($path in $plan.roots) { $scan += @('--root', [string]$path) }
foreach ($path in $plan.terminals) { $scan += @('--terminal', [string]$path) }
$oldToken = [Environment]::GetEnvironmentVariable('DASHBOARD_COLLECTOR_TOKEN', 'Process')
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($CollectorToken)
try {
    $env:DASHBOARD_COLLECTOR_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
try {
    Invoke-Python $scan
} finally {
    [Environment]::SetEnvironmentVariable('DASHBOARD_COLLECTOR_TOKEN', $oldToken, 'Process')
}
$save = @{
    Mode = 'Save'; ProjectDir = $ProjectDir; ServerUrl = $ServerUrl; PythonExe = $PythonExe
    CollectorDb = 'collector-central.db'; Inventory = 'inventory.json'; CollectorToken = $CollectorToken
    Roots = @($plan.roots); Terminals = @($plan.terminals)
}
if ($Profile -ne 'Logs') { $save.Accounts = 'accounts.json' }
# SecureString stays in this PowerShell process, never in task arguments.
& (Join-Path $PSScriptRoot 'new_host_task.ps1') @save
if ($Profile -eq 'Full') {
    & (Join-Path $PSScriptRoot 'monitor_task.ps1') -Mode Save -ProjectDir $ProjectDir `
        -SavedSettings '.dashboard-monitor.local.json' -PythonExe $PythonExe `
        -TelegramBotToken $TelegramBotToken -TelegramChatId $TelegramChatId
}
& (Join-Path $PSScriptRoot 'new_host_task.ps1') -Mode Install -ProjectDir $ProjectDir
if ($Profile -eq 'Full') {
    & (Join-Path $PSScriptRoot 'monitor_task.ps1') -Mode Install -ProjectDir $ProjectDir -Url $ServerUrl -SavedSettings '.dashboard-monitor.local.json'
    Start-ScheduledTask -TaskName 'MT5DashboardMonitor'
}
Start-ScheduledTask -TaskName 'MT5CollectorRemote'
Write-Host 'Tasks started. For Full: start exactly one DashboardEaProbe service per MT5; leave its Algo Trading and Signals permissions disabled.'
Write-Host 'Wait for the first account cycle, then run -Mode Check with the installed -Profile. Finish by verifying this host in the central dashboard.'
