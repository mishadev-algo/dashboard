# Runs only with a generated fixture project; all task and ACL mutations are mocked.
param([string]$ProjectDir, [string]$PythonExe, [ValidateSet('Logs','Accounts','Full')][string]$Profile,
      [ValidateSet('Success','UploadFailure','ExistingTask')][string]$Scenario = 'Success')
$ErrorActionPreference = 'Stop'
$global:DashboardFixtureRegistered = @{}
$global:DashboardFixtureStarted = @()
$global:DashboardFixtureScenario = $Scenario
function Get-ScheduledTask {
    param($TaskName, $ErrorAction)
    if ($global:DashboardFixtureScenario -eq 'ExistingTask') { return @{TaskName='MT5CollectorRemote'} }
    return $null
}
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory); return @{Execute=$Execute; Argument=$Argument; WorkingDirectory=$WorkingDirectory} }
function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User); return @{User=$User} }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel); return @{UserId=$UserId; LogonType=$LogonType} }
function New-ScheduledTaskSettingsSet {
    param($MultipleInstances, $RestartCount, $RestartInterval, $ExecutionTimeLimit, [switch]$StartWhenAvailable)
    return @{MultipleInstances=$MultipleInstances}
}
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Principal, $Settings, $Description, [switch]$Force)
    $global:DashboardFixtureRegistered[$TaskName] = $Action
}
function Start-ScheduledTask { param($TaskName); $global:DashboardFixtureStarted += $TaskName }
function icacls { $global:LASTEXITCODE=0 } # Fixture secrets only; no real ACL change.

$planDir = Join-Path $ProjectDir 'host-setup'
$tokenText = 'fixture-token-never-use-in-production-1234567890'
$token = ConvertTo-SecureString $tokenText -AsPlainText -Force
$bot = ConvertTo-SecureString 'fixture-bot' -AsPlainText -Force
try {
    & (Join-Path $ProjectDir 'scripts\setup_host.ps1') -Mode Install -ProjectDir $ProjectDir `
        -PlanDir $planDir -CollectorToken $token -TelegramBotToken $bot -TelegramChatId '123'
    if ($Scenario -ne 'Success') { throw 'Installer should have refused this scenario' }
} catch {
    if ($Scenario -eq 'Success' -or $_.Exception.Message -eq 'Installer should have refused this scenario') { throw }
    if ($global:DashboardFixtureRegistered.Count -or $global:DashboardFixtureStarted.Count) { throw 'Task mutation before validation succeeded' }
    if (Test-Path -LiteralPath (Join-Path $ProjectDir '.dashboard-remote.local.json')) { throw 'Settings saved after failed validation' }
    Write-Host "Fixture refusal passed: $Scenario"
    exit 0
}

$config = Get-Content -LiteralPath (Join-Path $ProjectDir '.dashboard-remote.local.json') -Raw | ConvertFrom-Json
if (($Profile -ne 'Logs') -and $config.accounts -ne 'accounts.json') { throw 'Account worker not saved' }
if (($Profile -eq 'Logs') -and $config.accounts) { throw 'Logs profile saved an account worker' }
if ($config.collector_token -eq $tokenText) { throw 'Token stored in plaintext' }
if (-not $global:DashboardFixtureRegistered.ContainsKey('MT5CollectorRemote')) { throw 'Collector task not registered' }
if ($global:DashboardFixtureStarted -notcontains 'MT5CollectorRemote') { throw 'Collector task not started' }
if ($Profile -eq 'Full') {
    if (-not $global:DashboardFixtureRegistered.ContainsKey('MT5DashboardMonitor')) { throw 'Monitor not registered' }
    if ($global:DashboardFixtureStarted -notcontains 'MT5DashboardMonitor') { throw 'Monitor not started' }
    if ($global:DashboardFixtureRegistered['MT5DashboardMonitor'].Argument -notlike '*.dashboard-monitor.local.json*') { throw 'Monitor uses legacy config' }
    $settings = Get-Content -LiteralPath (Join-Path $ProjectDir '.dashboard-monitor.local.json') -Raw | ConvertFrom-Json
    if ($settings.secrets.DASHBOARD_TELEGRAM_BOT_TOKEN -eq 'fixture-bot') { throw 'Bot token stored in plaintext' }
} elseif ($global:DashboardFixtureRegistered.ContainsKey('MT5DashboardMonitor')) { throw 'Unexpected monitor task' }
$actions = $global:DashboardFixtureRegistered | ConvertTo-Json -Depth 5
if ($actions.Contains($tokenText) -or $actions.Contains('fixture-bot')) { throw 'Secret in task arguments' }
Write-Host "Fixture installation passed: $Profile"
