param([string]$Toolchain)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if ($Toolchain) {
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
    & (Join-Path $PSScriptRoot 'sync-engine.ps1')
    & (Join-Path $taskRoot 'runtime\python.exe') -I -X utf8 (Join-Path $PSScriptRoot 'generate-notices.py')
    if ($LASTEXITCODE -ne 0) { throw 'Release license notices could not be generated' }
    npm run desktop:build
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
} finally { Pop-Location }
