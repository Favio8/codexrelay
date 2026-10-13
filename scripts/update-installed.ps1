param([string]$InstallDirectory)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskVersion = (Get-Content -LiteralPath (Join-Path $taskRoot 'package.json') -Raw | ConvertFrom-Json).version
$taskInstaller = Join-Path $taskRoot "src-tauri\target\release\bundle\nsis\CodexRelay_${taskVersion}_x64-setup.exe"
if (-not (Test-Path -LiteralPath $taskInstaller)) { throw 'Build the installer first' }
$taskArguments = '/S /UPDATE /R'
if ($InstallDirectory) { $taskArguments += ' /D=' + [IO.Path]::GetFullPath($InstallDirectory) }
$taskProcess = Start-Process -FilePath $taskInstaller -ArgumentList $taskArguments -WindowStyle Hidden -PassThru
$taskProcess.WaitForExit()
if ($taskProcess.ExitCode -ne 0) { throw "Installer failed ($($taskProcess.ExitCode)); see CodexRelay\upgrades\last-upgrade.json" }
$taskState = if ($env:CODEXRELAY_HOME) { $env:CODEXRELAY_HOME } else { Join-Path $env:LOCALAPPDATA 'CodexRelay' }
$taskReport = Get-Content -LiteralPath (Join-Path $taskState 'upgrades\last-upgrade.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $taskReport.finished -or $taskReport.maintenance_error -or $taskReport.version -ne $taskVersion) { throw 'Upgrade completion was not verified' }
$taskReport | ConvertTo-Json -Depth 8
