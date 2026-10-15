param([string]$Toolchain, [switch]$NoBundle, [switch]$Public)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if ($Public) {
    if ($env:RUSTFLAGS -and -not $env:CARGO_ENCODED_RUSTFLAGS) { throw 'Use CARGO_ENCODED_RUSTFLAGS to preserve custom flags in a public build' }
    $taskFlags = @()
    if ($env:CARGO_ENCODED_RUSTFLAGS) { $taskFlags = @($env:CARGO_ENCODED_RUSTFLAGS.Split([char]31)) }
    $taskProfile = [Environment]::GetFolderPath('UserProfile')
    $taskFlags += '--remap-path-prefix=' + $taskRoot + '=/build/codexrelay'
    $taskFlags += '--remap-path-prefix=' + $taskProfile + '=/build/user'
    $env:CARGO_ENCODED_RUSTFLAGS = [string]::Join([char]31, $taskFlags)
}
if ($Toolchain) {
    $Toolchain = (Resolve-Path -LiteralPath $Toolchain).Path
    $taskVc = Get-ChildItem (Join-Path $Toolchain 'VC\Tools\MSVC') -Directory | Sort-Object Name -Descending | Select-Object -First 1
    $taskSdkRoot = Join-Path $Toolchain 'Windows Kits\10'
    $taskSdk = Get-ChildItem (Join-Path $taskSdkRoot 'Lib') -Directory | Sort-Object Name -Descending | Select-Object -First 1
    $env:VCToolsInstallDir = $taskVc.FullName + '\'
    $env:VCToolsVersion = $taskVc.Name
    $env:WindowsSdkDir = $taskSdkRoot + '\'
    $env:WindowsSDKVersion = $taskSdk.Name + '\'
    $env:VSCMD_ARG_TGT_ARCH = 'x64'
    $env:VSCMD_ARG_HOST_ARCH = 'x64'
    $env:PATH = (Join-Path $taskVc.FullName 'bin\Hostx64\x64') + ';' + (Join-Path $taskSdkRoot "bin\$($taskSdk.Name)\x64") + ';' + $env:PATH
    $env:LIB = (Join-Path $taskVc.FullName 'lib\x64') + ';' + (Join-Path $taskSdk.FullName 'ucrt\x64') + ';' + (Join-Path $taskSdk.FullName 'um\x64')
    $env:INCLUDE = (Join-Path $taskVc.FullName 'include') + ';' + (Join-Path $taskSdkRoot "Include\$($taskSdk.Name)\ucrt") + ';' + (Join-Path $taskSdkRoot "Include\$($taskSdk.Name)\shared") + ';' + (Join-Path $taskSdkRoot "Include\$($taskSdk.Name)\um")
}
Push-Location $taskRoot
try {
    & (Join-Path $PSScriptRoot 'prepare-pi-runtime.ps1')
    & (Join-Path $PSScriptRoot 'sync-engine.ps1')
    & (Join-Path $taskRoot 'runtime\python.exe') -I -B -X utf8 (Join-Path $PSScriptRoot 'generate-notices.py')
    if ($LASTEXITCODE -ne 0) { throw 'Release license notices could not be generated' }
    if ($Public) {
        & (Join-Path $taskRoot 'runtime\python.exe') -I -B -X utf8 (Join-Path $PSScriptRoot 'prepare-public-runtime.py')
        if ($LASTEXITCODE -ne 0) { throw 'Public runtime still contains local build metadata' }
    }
    if ($NoBundle) { npm run tauri -- build --no-bundle }
    else { npm run desktop:build }
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
} finally { Pop-Location }
