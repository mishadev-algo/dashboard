param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Save', 'Install', 'Run')]
    [string]$Mode,
    [string]$ProjectDir,
    [string]$ServerUrl,
    [string]$PythonExe,
    [string]$CollectorDb = 'collector-central.db',
    [string]$Inventory = 'inventory.json',
    [string[]]$Roots = @(),
    [string[]]$Terminals = @(),
    [Security.SecureString]$CollectorToken,
    [switch]$Force,
    [string]$TaskName = 'MT5CollectorRemote'
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This script requires Windows and the MT5 user.' }
$scriptPath = $MyInvocation.MyCommand.Path
if (-not $scriptPath) { $scriptPath = $PSCommandPath }
if (-not $scriptPath) { throw 'Run this script with -File.' }
if (-not $ProjectDir) { $ProjectDir = Split-Path -Parent (Split-Path -Parent $scriptPath) }
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
$configPath = Join-Path $ProjectDir '.dashboard-remote.local.json'
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

if ($Mode -eq 'Save') {
    if (-not $ServerUrl -or -not $PythonExe) {
        throw 'Save requires -ServerUrl and the full path in -PythonExe.'
    }
    $uri = $null
    if (-not [Uri]::TryCreate($ServerUrl, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or -not $uri.Host -or $uri.UserInfo -or
        $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) {
        throw 'ServerUrl must be an HTTPS origin without a path, query, or credentials.'
    }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw 'PythonExe does not exist.'
    }
    foreach ($name in @($CollectorDb, $Inventory)) {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir $name) -PathType Leaf)) {
            throw "Required host file is missing: $name"
        }
    }
    if ((Test-Path -LiteralPath $configPath) -and -not $Force) {
        throw 'Remote settings already exist; pass -Force only for an intentional replacement.'
    }
    if (-not $CollectorToken) {
        $CollectorToken = Read-Host -Prompt 'Paste this host collector token' -AsSecureString
    }
    if (-not $CollectorToken -or $CollectorToken.Length -lt 32) {
        throw 'Collector token must contain at least 32 characters.'
    }
    if (-not $Roots) {
        $Roots = @((Join-Path $env:APPDATA 'MetaQuotes\Terminal'))
    }
    $config = @{
        version = 1
        user_sid = $currentSid
        python = (Resolve-Path -LiteralPath $PythonExe).Path
        collector_db = $CollectorDb
        inventory = $Inventory
        roots = @($Roots)
        terminals = @($Terminals)
        server_url = $uri.GetLeftPart([UriPartial]::Authority)
        collector_token = ConvertFrom-SecureString -SecureString $CollectorToken
    }
    $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
    $null = & icacls $configPath /inheritance:r /grant:r ('*' + $currentSid + ':(F)') '*S-1-5-18:(F)' '*S-1-5-32-544:(F)'
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict remote settings permissions.' }
    Write-Host "Saved encrypted collector settings for $([Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    exit 0
}

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw 'Remote settings are missing. Run -Mode Save first.'
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.version -ne 1 -or $config.user_sid -ne $currentSid) {
    throw 'Remote settings belong to another Windows user or version.'
}
if (-not (Test-Path -LiteralPath ([string]$config.python) -PathType Leaf)) {
    throw 'Saved Python executable is missing.'
}

if ($Mode -eq 'Install') {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $scriptPath + '" -Mode Run -ProjectDir "' + $ProjectDir + '"'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $ProjectDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description 'MT5 log collector to central VPS' -Force | Out-Null
    Write-Host "Installed $TaskName for $user. Start the task after the central token is registered."
    exit 0
}

$secure = ConvertTo-SecureString -String ([string]$config.collector_token)
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    [Environment]::SetEnvironmentVariable(
        'DASHBOARD_COLLECTOR_TOKEN', [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer), 'Process'
    )
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
$arguments = @(
    '-u', '-m', 'collector.run_host',
    '--collector-db', [string]$config.collector_db,
    '--inventory', [string]$config.inventory,
    '--server-url', [string]$config.server_url
)
foreach ($path in $config.roots) { $arguments += @('--root', [string]$path) }
foreach ($path in $config.terminals) { $arguments += @('--terminal', [string]$path) }

Set-Location -LiteralPath $ProjectDir
[Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'Process')
$logDirectory = Join-Path $ProjectDir 'run-logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logFile = Join-Path $logDirectory ("remote-collector-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
"Starting remote collector at $(Get-Date -Format o)" | Out-File -FilePath $logFile -Encoding Unicode
& ([string]$config.python) @arguments *>> $logFile
exit $LASTEXITCODE
