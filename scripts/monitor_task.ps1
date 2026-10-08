param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Save', 'Install')]
    [string]$Mode,
    [string]$ProjectDir,
    [string]$TaskName = 'MT5DashboardMonitor',
    [string]$SavedSettings = '.dashboard-start.local.json',
    [string]$PythonExe,
    [Security.SecureString]$TelegramBotToken,
    [string]$TelegramChatId,
    [string]$Url = 'http://127.0.0.1:8765'
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This script requires Windows.' }
$scriptPath = $MyInvocation.MyCommand.Path
if (-not $scriptPath) { $scriptPath = $PSCommandPath }
if (-not $scriptPath) { throw 'Cannot determine script path; run with -File.' }
if (-not $ProjectDir) { $ProjectDir = Split-Path -Parent (Split-Path -Parent $scriptPath) }
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
if ($Mode -eq 'Save') {
    if (-not $PSBoundParameters.ContainsKey('SavedSettings')) { $SavedSettings = '.dashboard-monitor.local.json' }
    $destination = Join-Path $ProjectDir $SavedSettings
    if (Test-Path -LiteralPath $destination) { throw 'Monitor settings already exist; review them before replacement.' }
    if (-not $PythonExe -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Save requires the full PythonExe path.' }
    if (-not $TelegramBotToken) { $TelegramBotToken = Read-Host 'Telegram bot token' -AsSecureString }
    if (-not $TelegramChatId) { $TelegramChatId = Read-Host 'Telegram chat ID' }
    if (-not $TelegramBotToken -or $TelegramBotToken.Length -eq 0 -or -not $TelegramChatId.Trim()) { throw 'Telegram bot token and chat ID are required.' }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $chat = ConvertTo-SecureString -String $TelegramChatId -AsPlainText -Force
    @{
        version = 1; user_sid = $sid; python = (Resolve-Path -LiteralPath $PythonExe).Path
        secrets = @{
            DASHBOARD_TELEGRAM_BOT_TOKEN = ConvertFrom-SecureString $TelegramBotToken
            DASHBOARD_TELEGRAM_CHAT_ID = ConvertFrom-SecureString $chat
        }
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $destination -Encoding UTF8
    $null = & icacls $destination /inheritance:r /grant:r ('*' + $sid + ':(F)') '*S-1-5-18:(F)' '*S-1-5-32-544:(F)'
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict monitor settings permissions.' }
    Write-Host 'Saved independent encrypted monitor settings.'
    return
}
$configPath = Join-Path $ProjectDir $SavedSettings
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Monitor settings are missing: $configPath. Use -Mode Save with the same -SavedSettings first."
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
if ($SavedSettings.Contains('"')) { throw 'SavedSettings must not contain a quote' }

$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$pythonw = Join-Path (Split-Path -Parent ([string]$config.python)) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "Python windowless executable is missing: $pythonw"
}
$arguments = '-m server.uptime_monitor --url "' + $Url + '" ' +
             '--state ".dashboard-monitor-state.json" ' +
             '--saved-settings "' + $SavedSettings + '" ' +
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
