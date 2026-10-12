param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskInstall = Join-Path $taskRoot '.tmp\installed app'
$taskState = Join-Path $taskRoot '.tmp\installer-state'
$taskCodex = Join-Path $taskRoot '.tmp\installer-codex'
if (Test-Path -LiteralPath $taskInstall) { throw 'Use a fresh installer test directory' }
$taskExisting = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue | Get-ItemProperty | Where-Object DisplayName -eq 'CodexRelay'
if ($taskExisting) { throw 'CodexRelay is already installed; do not overwrite it during tests' }
$env:CODEXRELAY_HOME = $taskState
$env:CODEX_HOME = $taskCodex
New-Item -ItemType Directory -Force -Path $taskCodex | Out-Null
Set-Content -LiteralPath (Join-Path $taskCodex 'config.toml') -Value '# preserve user setting
model="original-user-model"
' -Encoding utf8
$taskProcess = Start-Process -FilePath $Installer -ArgumentList "/S /D=$taskInstall" -WindowStyle Hidden -PassThru -Wait
if ($taskProcess.ExitCode -ne 0) { throw "Installer returned $($taskProcess.ExitCode)" }
$taskPython = Join-Path $taskInstall 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Bundled runtime missing' }
$taskSnapshot = '{"method":"snapshot"}' | & $taskPython -I -X utf8 -m codexrelay rpc | ConvertFrom-Json
if (-not $taskSnapshot.ok) { throw 'Installed runtime failed snapshot' }
$taskRegistered = '{"method":"integration_install"}' | & $taskPython -I -X utf8 -m codexrelay rpc | ConvertFrom-Json
if (-not $taskRegistered.ok) { throw "Installed registration failed: $($taskRegistered.error)" }
Add-Content -LiteralPath (Join-Path $taskCodex 'config.toml') -Value '[later_user_change]
keep=true' -Encoding utf8
foreach ($taskName in @('state.py','mcp_server.py','__main__.py')) {
    $taskSource = Get-FileHash -LiteralPath (Join-Path $taskRoot "engine\codexrelay\$taskName")
    $taskCopy = Get-FileHash -LiteralPath (Join-Path $taskInstall "runtime\Lib\site-packages\codexrelay\$taskName")
    if ($taskSource.Hash -ne $taskCopy.Hash) { throw "Packaged source mismatch: $taskName" }
}
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--remote-debugging-port=9224'
$taskApp = Start-Process -FilePath (Join-Path $taskInstall 'codexrelay.exe') -WindowStyle Hidden -PassThru
try {
    node (Join-Path $PSScriptRoot 'installed-window.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Installed desktop window failed' }
} finally { Stop-Process -Id $taskApp.Id -ErrorAction SilentlyContinue }
$taskUninstall = Start-Process -FilePath (Join-Path $taskInstall 'uninstall.exe') -ArgumentList '/S' -WindowStyle Hidden -PassThru -Wait
if ($taskUninstall.ExitCode -ne 0) { throw "Uninstaller returned $($taskUninstall.ExitCode)" }
# NSIS may hand off to a temporary uninstaller process; observe its actual result.
$taskDeadline = (Get-Date).AddSeconds(30)
do {
    $taskConfig = Get-Content -LiteralPath (Join-Path $taskCodex 'config.toml') -Raw
    if ($taskConfig -notmatch 'mcp_servers.codexrelay') { break }
    Start-Sleep -Milliseconds 200
} while ((Get-Date) -lt $taskDeadline)
if ($taskConfig -match 'mcp_servers.codexrelay') { throw 'Uninstaller did not remove the managed registration' }
if ($taskConfig -notmatch 'original-user-model' -or $taskConfig -notmatch 'later_user_change') { throw 'Uninstaller lost user settings' }
$taskReport = @{ ok=$true; installerExitCode=$taskProcess.ExitCode; uninstallerExitCode=$taskUninstall.ExitCode; checks=@('silent current-user installation','runtime in path with spaces','installed source hashes match','actual installed desktop window and IPC','uninstall hook removes registration','later user settings preserved') }
$taskReport | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskRoot '.tmp\installer-report.json') -Encoding utf8
$taskReport | ConvertTo-Json -Depth 4
