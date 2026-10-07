param(
    [string]$ProjectDir,
    [string]$Output = '.deploy/central.env',
    [string]$HostId = 'vps72565826'
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This script requires Windows DPAPI.' }
if (-not $ProjectDir) { $ProjectDir = Split-Path -Parent $PSScriptRoot }
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path
$sourcePath = Join-Path $ProjectDir '.dashboard-start.local.json'
$outputPath = Join-Path $ProjectDir $Output
if (Test-Path -LiteralPath $outputPath) { throw "Refusing to overwrite $outputPath" }
$source = Get-Content -LiteralPath $sourcePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($source.version -ne 1 -or
    $source.user_sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
    throw 'Existing settings belong to a different Windows user or version.'
}

$names = @('DASHBOARD_HOST_TOKENS', 'DASHBOARD_COLLECTOR_TOKEN',
           'DASHBOARD_TELEGRAM_BOT_TOKEN', 'DASHBOARD_TELEGRAM_CHAT_ID')
$plain = @{}
foreach ($name in $names) {
    $encrypted = $source.secrets.PSObject.Properties[$name]
    if (-not $encrypted) { throw "Existing setting $name is missing" }
    $secure = ConvertTo-SecureString -String ([string]$encrypted.Value)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain[$name] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    if (-not $plain[$name] -or $plain[$name] -match "['`r`n]") {
        throw "Setting $name is empty or cannot be represented safely in EnvironmentFile"
    }
}
$map = $plain.DASHBOARD_HOST_TOKENS | ConvertFrom-Json
$hostToken = $map.PSObject.Properties[$HostId]
if (-not $hostToken -or $hostToken.Value -ne $plain.DASHBOARD_COLLECTOR_TOKEN) {
    throw "Existing host token does not match $HostId"
}

$directory = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$lines = @(
    "DASHBOARD_HOST_TOKENS='$($plain.DASHBOARD_HOST_TOKENS)'"
    'DASHBOARD_POSTGRES_DSN=postgresql:///dashboard'
    "DASHBOARD_TELEGRAM_BOT_TOKEN='$($plain.DASHBOARD_TELEGRAM_BOT_TOKEN)'"
    "DASHBOARD_TELEGRAM_CHAT_ID='$($plain.DASHBOARD_TELEGRAM_CHAT_ID)'"
)
[IO.File]::WriteAllText($outputPath, (($lines -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
$null = & icacls $outputPath /inheritance:r /grant:r '*S-1-5-32-544:(F)' '*S-1-5-18:(F)'
if ($LASTEXITCODE -ne 0) { throw "Could not restrict permissions on $outputPath" }
Write-Output "Private central environment written to $outputPath; values not displayed."
