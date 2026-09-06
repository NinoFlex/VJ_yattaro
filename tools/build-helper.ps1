param([string]$Runtime = 'win-x64')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$project = Join-Path $root 'native\ShazamWebViewBridge'
Set-Location $project
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_NOLOGO = '1'

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $File" }
}
function Test-Sdk {
    param([string]$Path)
    try {
        $lines = & $Path --list-sdks 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        foreach ($line in $lines) {
            if ($line -match '^(\d+)\.') {
                if ([int]$Matches[1] -ge 8) { return $true }
            }
        }
    } catch { }
    return $false
}
try {
    $dotnet = $null
    $candidates = @()
    $command = Get-Command dotnet.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $candidates += (Join-Path $env:LOCALAPPDATA 'ShazamWatch\dotnet-sdk-8\dotnet.exe')
    $candidates += (Join-Path $env:LOCALAPPDATA 'VJ_yattaro\dotnet-sdk-8\dotnet.exe')
    foreach ($candidate in $candidates) {
        if ((Test-Path $candidate) -and (Test-Sdk $candidate)) { $dotnet = $candidate; break }
    }
    if (-not $dotnet) {
        $installDir = Join-Path $env:LOCALAPPDATA 'VJ_yattaro\dotnet-sdk-8'
        New-Item -ItemType Directory -Force $installDir | Out-Null
        $installer = Join-Path $installDir ('dotnet-install-' + [guid]::NewGuid().ToString('N') + '.ps1')
        Write-Host '[INFO] Installing a private .NET 8 SDK from Microsoft (no admin required).'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        try {
            Invoke-WebRequest -UseBasicParsing 'https://dot.net/v1/dotnet-install.ps1' -OutFile $installer
            Invoke-Checked 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installer,
                '-Channel', '8.0', '-Quality', 'GA', '-Architecture', 'x64', '-InstallDir', $installDir, '-NoPath')
        } finally { if (Test-Path $installer) { Remove-Item -Force $installer } }
        $dotnet = Join-Path $installDir 'dotnet.exe'
        if (-not (Test-Sdk $dotnet)) { throw 'The .NET SDK could not be initialized.' }
    }
    Write-Host "[INFO] .NET SDK: $dotnet"
    Invoke-Checked $dotnet @('--version')
    $publish = Join-Path $project 'publish'
    if (Test-Path $publish) { Remove-Item -Recurse -Force $publish }
    Invoke-Checked $dotnet @('publish', 'ShazamWebViewBridge.csproj', '-c', 'Release',
        '-r', $Runtime, '--self-contained', 'true', '-p:PublishSingleFile=false',
        '-p:DebugType=None', '-p:DebugSymbols=false', '-o', $publish)
    if (-not (Test-Path (Join-Path $publish 'ShazamWebViewBridge.exe'))) { throw 'Helper EXE not produced.' }
    Write-Host "[OK] Helper: $publish\ShazamWebViewBridge.exe"
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
exit 0
