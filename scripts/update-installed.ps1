param([string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\CodexRelay'))
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskExecutable = Join-Path $InstallDirectory 'codexrelay.exe'
$taskPython = Join-Path $InstallDirectory 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $taskExecutable)) { throw 'CodexRelay is not installed at the selected location' }
& $taskPython -I -X utf8 -m codexrelay check-idle
if ($LASTEXITCODE -ne 0) { throw 'Finish the active subagent task before updating' }
$taskVersion = (Get-Content (Join-Path $taskRoot 'package.json') -Raw | ConvertFrom-Json).version
$taskBackup = Join-Path $taskRoot ('.tmp\app-update-backup-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $taskBackup | Out-Null
$taskChanges = [System.Collections.Generic.List[object]]::new()
$taskChanges.Add(@{Source=(Join-Path $taskRoot 'src-tauri\target\release\codexrelay.exe');Destination=$taskExecutable;Backup=(Join-Path $taskBackup 'codexrelay.exe')})
foreach ($taskModule in @('codexrelay','deepseek_mcp')) {
    $taskSourceDirectory = Join-Path $taskRoot "runtime\Lib\site-packages\$taskModule"
    foreach ($taskSource in Get-ChildItem -LiteralPath $taskSourceDirectory -Recurse -File -Filter '*.py') {
        $taskRelative = [IO.Path]::GetRelativePath($taskSourceDirectory,$taskSource.FullName)
        $taskDestination = Join-Path $InstallDirectory "runtime\Lib\site-packages\$taskModule\$taskRelative"
        if (-not (Test-Path -LiteralPath $taskDestination) -or (Get-FileHash -LiteralPath $taskSource.FullName).Hash -ne (Get-FileHash -LiteralPath $taskDestination).Hash) {
            $taskChanges.Add(@{Source=$taskSource.FullName;Destination=$taskDestination;Backup=(Join-Path $taskBackup "$taskModule\$taskRelative")})
        }
    }
}
foreach ($taskSource in Get-ChildItem -LiteralPath (Join-Path $taskRoot 'runtime\pi') -File) {
    $taskDestination = Join-Path $InstallDirectory ('runtime\pi\' + $taskSource.Name)
    if (-not (Test-Path -LiteralPath $taskDestination) -or (Get-FileHash -LiteralPath $taskSource.FullName).Hash -ne (Get-FileHash -LiteralPath $taskDestination).Hash) {
        $taskChanges.Add(@{Source=$taskSource.FullName;Destination=$taskDestination;Backup=(Join-Path $taskBackup ('pi\' + $taskSource.Name))})
    }
}
foreach ($taskName in @('CODEXRELAY-LICENSE.txt','DEEPSEEK-LICENSE.txt','THIRD-PARTY-NOTICES.txt')) {
    $taskSource = Join-Path $taskRoot "runtime\$taskName"
    $taskDestination = Join-Path $InstallDirectory "runtime\$taskName"
    if (-not (Test-Path -LiteralPath $taskDestination) -or (Get-FileHash -LiteralPath $taskSource).Hash -ne (Get-FileHash -LiteralPath $taskDestination).Hash) {
        $taskChanges.Add(@{Source=$taskSource;Destination=$taskDestination;Backup=(Join-Path $taskBackup $taskName)})
    }
}
foreach ($taskChange in $taskChanges) {
    $taskChange.Existed = Test-Path -LiteralPath $taskChange.Destination
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $taskChange.Backup) | Out-Null
    if (Test-Path -LiteralPath $taskChange.Destination) { Copy-Item -LiteralPath $taskChange.Destination -Destination $taskChange.Backup }
}
$taskCodexDirectory = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$taskConfigHashes = @{}
foreach ($taskName in @('config.toml','AGENTS.md')) {
    $taskPath = Join-Path $taskCodexDirectory $taskName
    if (Test-Path -LiteralPath $taskPath) { $taskConfigHashes[$taskName]=(Get-FileHash -LiteralPath $taskPath).Hash }
}
$taskRegistry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CodexRelay'
$taskRegistration = Get-ItemProperty -LiteralPath $taskRegistry
if ($taskRegistration.InstallLocation.Trim('"') -ne $InstallDirectory) { throw 'Installed application registration differs from the selected location' }
$taskOldVersion = $taskRegistration.DisplayVersion
$taskGuiProcesses = @(Get-Process codexrelay -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $taskExecutable })
foreach ($taskGui in $taskGuiProcesses) {
    Stop-Process -Id $taskGui.Id
    if (-not $taskGui.WaitForExit(10000)) { throw 'The application window has not finished closing' }
    $taskGui.Dispose()
}
$taskApplied = [System.Collections.Generic.List[object]]::new()
try {
    foreach ($taskChange in $taskChanges) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $taskChange.Destination) | Out-Null
        $taskApplied.Add($taskChange)
        Copy-Item -LiteralPath $taskChange.Source -Destination $taskChange.Destination -Force
        if ((Get-FileHash -LiteralPath $taskChange.Source).Hash -ne (Get-FileHash -LiteralPath $taskChange.Destination).Hash) { throw 'Application update hash mismatch' }
    }
    Set-ItemProperty -LiteralPath $taskRegistry -Name DisplayVersion -Value $taskVersion
    $taskStatus = '{"method":"integration_preview"}' | & $taskPython -I -X utf8 -m codexrelay rpc | ConvertFrom-Json
    if (-not $taskStatus.ok -or -not $taskStatus.data.registered -or $taskStatus.data.conflict) { throw 'Updated runtime does not recognize the existing registration' }
    foreach ($taskName in $taskConfigHashes.Keys) {
        if ((Get-FileHash -LiteralPath (Join-Path $taskCodexDirectory $taskName)).Hash -ne $taskConfigHashes[$taskName]) { throw 'Unexpected Codex configuration change during update' }
    }
    $taskReport = @{ok=$true;version=$taskVersion;updatedFiles=$taskChanges.Count;installDirectory=$InstallDirectory;registered=$taskStatus.data.registered;codexConfigurationUnchanged=$true;backup=$taskBackup;codexRestartRequired=$true}
    New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot 'artifacts\evidence') | Out-Null
    $taskReport | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskRoot "artifacts\evidence\$taskVersion-installed-update.json") -Encoding utf8
    $taskReport | ConvertTo-Json -Depth 4
} catch {
    foreach ($taskChange in $taskApplied) {
        if (Test-Path -LiteralPath $taskChange.Backup) { Copy-Item -LiteralPath $taskChange.Backup -Destination $taskChange.Destination -Force }
        elseif (-not $taskChange.Existed) {
            $taskResolved = [IO.Path]::GetFullPath($taskChange.Destination)
            $taskInstallRoot = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\') + '\'
            if (-not $taskResolved.StartsWith($taskInstallRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Rollback target is outside the selected installation' }
            Remove-Item -LiteralPath $taskResolved -Force
        }
    }
    Set-ItemProperty -LiteralPath $taskRegistry -Name DisplayVersion -Value $taskOldVersion
    throw
} finally {
    Start-Process -FilePath $taskExecutable -WindowStyle Hidden
}
