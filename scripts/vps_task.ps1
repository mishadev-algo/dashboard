param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Save', 'Install', 'Run')]
    [string]$Mode,
    [string]$ProjectDir,
    [string]$CollectorDb = 'collector-central.db',
    [string]$Inventory = 'inventory.json',
    [string]$Accounts = 'accounts.json',
    [string[]]$Root = @(),
    [string[]]$Terminal = @(),
    [string]$TaskName = 'MT5Dashboard'
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This script requires Windows and the current MT5 user.' }
$scriptPath = $MyInvocation.MyCommand.Path
if (-not $scriptPath) { $scriptPath = $PSCommandPath }
if (-not $scriptPath) { throw 'Cannot determine script path; run with -File.' }
if (-not $ProjectDir) { $ProjectDir = Split-Path -Parent (Split-Path -Parent $scriptPath) }
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
$configPath = Join-Path $ProjectDir '.dashboard-start.local.json'
$secretNames = @(
    'DASHBOARD_HOST_TOKENS',
    'DASHBOARD_COLLECTOR_TOKEN',
    'DASHBOARD_TELEGRAM_BOT_TOKEN',
    'DASHBOARD_TELEGRAM_CHAT_ID'
)

if ($Mode -eq 'Save') {
    foreach ($name in $secretNames) {
        if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
            throw "$name is missing from this PowerShell window"
        }
    }
    foreach ($name in @('central.db', $CollectorDb, $Inventory, $Accounts)) {
        $candidate = Join-Path $ProjectDir $name
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Required file is missing: $candidate"
        }
    }
    $python = (& py -3.13 -c 'import sys; print(sys.executable)' | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Python 3.13 executable was not found'
    }
    $encrypted = @{}
    foreach ($name in $secretNames) {
        $plain = [Environment]::GetEnvironmentVariable($name, 'Process')
        $secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
        $encrypted[$name] = ConvertFrom-SecureString -SecureString $secure
    }
    $config = @{
        version = 1
        user_sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        python = $python
        collector_db = $CollectorDb
        inventory = $Inventory
        accounts = $Accounts
        roots = @($Root)
        terminals = @($Terminal)
        secrets = $encrypted
    }
    $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
    Write-Host "Saved encrypted startup settings in $configPath for $([Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    exit 0
}

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Startup settings are missing: $configPath. Run -Mode Save first."
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
        -Principal $principal -Settings $settings -Description 'MT5 dashboard services at trader logon' -Force | Out-Null
    Write-Host "Installed task $TaskName for $user. Stop the manual launcher before starting this task."
    exit 0
}

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.version -ne 1) { throw 'Unsupported startup settings version' }
if ($config.user_sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
    throw 'Startup settings were encrypted for a different Windows user'
}
foreach ($name in $secretNames) {
    $property = $config.secrets.PSObject.Properties[$name]
    if (-not $property) { throw "Encrypted setting is missing: $name" }
    $secure = ConvertTo-SecureString -String $property.Value
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        [Environment]::SetEnvironmentVariable(
            $name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer), 'Process'
        )
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$arguments = @(
    '-u', '-m', 'server.run_all',
    '--db', 'central.db',
    '--collector-db', [string]$config.collector_db,
    '--inventory', [string]$config.inventory,
    '--accounts', [string]$config.accounts
)
foreach ($path in $config.roots) { $arguments += @('--root', [string]$path) }
foreach ($path in $config.terminals) { $arguments += @('--terminal', [string]$path) }

Set-Location -LiteralPath $ProjectDir
$logDirectory = Join-Path $ProjectDir 'run-logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logFile = Join-Path $logDirectory ("dashboard-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
"Starting dashboard at $(Get-Date -Format o)" | Out-File -FilePath $logFile -Encoding UTF8
& ([string]$config.python) @arguments *>> $logFile
exit $LASTEXITCODE
