param(
    [ValidateSet('Prepare','Finish','Abort')][string]$Action,
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [Parameter(Mandatory=$true)][string]$PlanPath,
    [string]$UpgradeScript,
    [string]$ExpectedVersion
)
$ErrorActionPreference = 'Stop'
$taskTestRoot = $env:CODEXRELAY_INSTALL_TEST_ROOT
$taskLocalBase = if ($taskTestRoot) { Join-Path $taskTestRoot 'Local' } else { $env:LOCALAPPDATA }
$taskState = if ($env:CODEXRELAY_HOME) { $env:CODEXRELAY_HOME } else { Join-Path $taskLocalBase 'CodexRelay' }
$taskRegistryBase = if ($taskTestRoot) { 'Software\CodexRelay\InstallerTests\' + (Split-Path $taskTestRoot -Leaf) } else { 'Software' }
$taskProductKey = if ($taskTestRoot) { $taskRegistryBase + '\Product' } else { 'Software\codexrelay\CodexRelay' }
$taskUninstallKey = if ($taskTestRoot) { $taskRegistryBase + '\Uninstall' } else { 'Software\Microsoft\Windows\CurrentVersion\Uninstall\CodexRelay' }
$taskRegistry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryView]::Registry64)
$taskInstall = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
$taskReportDirectory = Join-Path $taskState 'upgrades'

function Test-RelayInstallation([string]$Directory) {
    if (-not $Directory) { return $false }
    $taskExe = Join-Path $Directory 'codexrelay.exe'
    return (Test-Path -LiteralPath $taskExe) -and
        ([Diagnostics.FileVersionInfo]::GetVersionInfo($taskExe).ProductName -eq 'CodexRelay') -and
        (Test-Path -LiteralPath (Join-Path $Directory 'runtime\Lib\site-packages\codexrelay\__init__.py'))
}
function Save-RelayPlan($Plan) {
    [IO.File]::WriteAllText($PlanPath,($Plan | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
}
function Invoke-RelayUpgrade([string]$Python,[string]$Step) {
    $taskPreference = $ErrorActionPreference
    try {
        # Windows PowerShell otherwise treats the first native stderr line as a
        # terminating error before we can read the actionable reason in the plan.
        $ErrorActionPreference = 'Continue'
        $taskOutput = if ($Step -eq 'prepare') { & $Python -I -B -X utf8 $UpgradeScript $Step $PlanPath 2>&1 }
            else { & $Python -I -B -X utf8 -m codexrelay.upgrade $Step $PlanPath 2>&1 }
        $taskExitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $taskPreference }
    if ($taskExitCode -ne 0) {
        $taskFailed = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
        throw $(if ($taskFailed.error) { $taskFailed.error } else { '升级状态处理失败，请重试。' })
    }
}
try {
    New-Item -ItemType Directory -Force -Path $taskReportDirectory | Out-Null
    if ($Action -eq 'Prepare') {
        if ($taskInstall -eq [IO.Path]::GetFullPath($taskState).TrimEnd('\')) { throw '安装目录不能与配置目录相同，请使用 Programs\CodexRelay。' }
        # Read and write must work before we can promise a registered upgrade.
        foreach ($taskKey in @($taskProductKey,$taskUninstallKey)) {
            $taskHandle = $taskRegistry.CreateSubKey($taskKey,$true)
            if (-not $taskHandle) { throw '无法更新安装登记，请从 Windows 资源管理器重新运行安装包。' }
            $taskHandle.Dispose()
        }
        $taskCandidates = @($taskInstall,(Join-Path $taskLocalBase 'Programs\CodexRelay'),(Join-Path $taskLocalBase 'CodexRelay'))
        foreach ($taskKey in @($taskProductKey,$taskUninstallKey)) {
            $taskHandle = $taskRegistry.OpenSubKey($taskKey)
            $taskValue = $taskHandle.GetValue($(if ($taskKey -eq $taskProductKey) { '' } else { 'InstallLocation' }))
            $taskHandle.Dispose()
            if ($taskValue) { $taskCandidates += ([string]$taskValue).Trim('"') }
        }
        $taskRoots = @($taskCandidates | Where-Object { Test-RelayInstallation $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') } | Sort-Object -Unique)
        foreach ($taskRoot in $taskRoots) {
            $taskVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $taskRoot 'codexrelay.exe')).ProductVersion
            if ([Version]$taskVersion -gt [Version]$ExpectedVersion) { throw '已安装更新版本，请使用对应的更新安装包。' }
        }
        $taskPlan = @{install_directory=$taskInstall;state_directory=$taskState;roots=$taskRoots;prepared=$false;version=$ExpectedVersion;stopped_processes=@();retired=@();started_at=[DateTime]::UtcNow.ToString('o')}
        Save-RelayPlan $taskPlan
        if ($taskRoots.Count) {
            Invoke-RelayUpgrade (Join-Path $taskRoots[0] 'runtime\python.exe') 'prepare'
            $taskPlan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $taskGuiPaths = @($taskRoots | ForEach-Object { Join-Path $_ 'codexrelay.exe' })
            $taskPythonPaths = @($taskRoots | ForEach-Object { Join-Path $_ 'runtime\python.exe' })
            $taskProcesses = @(Get-CimInstance Win32_Process | Where-Object {
                $taskIsGui = $taskGuiPaths -contains $_.ExecutablePath
                $taskIsEngine = ($taskPythonPaths -contains $_.ExecutablePath) -and $_.CommandLine -match '-m\s+codexrelay\s+mcp(?:\s|$)'
                $_.ExecutablePath -and ($taskIsGui -or $taskIsEngine)
            })
            foreach ($taskProcess in $taskProcesses) {
                $taskLive = Get-Process -Id $taskProcess.ProcessId -ErrorAction SilentlyContinue
                if (-not $taskLive) { continue }
                Stop-Process -Id $taskLive.Id
                if (-not $taskLive.WaitForExit(10000)) { throw '旧实例未退出，升级已停止。' }
                $taskPlan.stopped_processes += @{pid=$taskLive.Id;path=$taskProcess.ExecutablePath}
                $taskLive.Dispose()
            }
            Save-RelayPlan $taskPlan
        }
    } elseif ($Action -eq 'Finish') {
        $taskPlan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $taskHandle = $taskRegistry.OpenSubKey($taskUninstallKey)
        try {
            if ($taskHandle.GetValue('DisplayVersion') -ne $ExpectedVersion -or ([string]$taskHandle.GetValue('InstallLocation')).Trim('"') -ne $taskInstall) { throw '安装登记未更新，升级未完成。' }
        } finally { if ($taskHandle) { $taskHandle.Dispose() } }
        $taskHandle = $taskRegistry.OpenSubKey($taskProductKey)
        try {
            if ($taskHandle.GetValue('') -ne $taskInstall) { throw '安装位置登记未更新，升级未完成。' }
        } finally { if ($taskHandle) { $taskHandle.Dispose() } }
        if ([Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $taskInstall 'codexrelay.exe')).ProductVersion -ne $ExpectedVersion) { throw '安装程序版本校验失败。' }
        Invoke-RelayUpgrade (Join-Path $taskInstall 'runtime\python.exe') 'finish'
        $taskPlan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
        # Existing shortcuts must agree even when /UPDATE suppresses new shortcuts.
        $taskShell = New-Object -ComObject WScript.Shell
        $taskShortcutRoots = if ($taskTestRoot) { @((Join-Path $taskTestRoot 'StartMenu'),(Join-Path $taskTestRoot 'Desktop')) }
            else { @((Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),[Environment]::GetFolderPath('Desktop')) }
        foreach ($taskShortcutRoot in $taskShortcutRoots) {
            foreach ($taskLink in Get-ChildItem -LiteralPath $taskShortcutRoot -Filter '*CodexRelay*.lnk' -Recurse -ErrorAction SilentlyContinue) {
                $taskShortcut = $taskShell.CreateShortcut($taskLink.FullName)
                if (@($taskPlan.roots | ForEach-Object { Join-Path $_ 'codexrelay.exe' }) -contains $taskShortcut.TargetPath) {
                    $taskShortcut.TargetPath = Join-Path $taskInstall 'codexrelay.exe'
                    $taskShortcut.WorkingDirectory = $taskInstall
                    $taskShortcut.Save()
                }
            }
        }
        $taskBackup = Join-Path $taskReportDirectory ('retired-' + [Guid]::NewGuid().ToString('N'))
        foreach ($taskRoot in $taskPlan.roots) {
            if ($taskRoot -eq $taskInstall) { continue }
            $taskDestinationRoot = Join-Path $taskBackup ([Guid]::NewGuid().ToString('N'))
            foreach ($taskName in @('codexrelay.exe','runtime','uninstall.exe')) {
                $taskSource = [IO.Path]::GetFullPath((Join-Path $taskRoot $taskName))
                $taskBackupName = if ($taskName.EndsWith('.exe')) { $taskName + '.backup' } else { $taskName }
                $taskDestination = [IO.Path]::GetFullPath((Join-Path $taskDestinationRoot $taskBackupName))
                if (-not $taskSource.StartsWith(([IO.Path]::GetFullPath($taskRoot).TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase) -or
                    -not $taskDestination.StartsWith(([IO.Path]::GetFullPath($taskBackup).TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)) { throw '旧安装迁移路径校验失败。' }
                if (Test-Path -LiteralPath $taskSource) {
                    New-Item -ItemType Directory -Force -Path $taskDestinationRoot | Out-Null
                    Move-Item -LiteralPath $taskSource -Destination $taskDestination
                    $taskPlan.retired += @{source=$taskSource;backup=$taskDestination}
                }
            }
        }
        $taskPlan | Add-Member -NotePropertyName completed_at -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        Save-RelayPlan $taskPlan
        Copy-Item -LiteralPath $PlanPath -Destination (Join-Path $taskReportDirectory 'last-upgrade.json') -Force
    } elseif (Test-Path -LiteralPath $PlanPath) {
        $taskPlan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($taskPlan.prepared) {
            $taskPython = Join-Path $taskInstall 'runtime\python.exe'
            if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = Join-Path $taskPlan.roots[0] 'runtime\python.exe' }
            & $taskPython -I -B -X utf8 $UpgradeScript abort $PlanPath 2>&1 | Out-Null
        }
    }
} catch {
    $taskMessage = $_.Exception.Message
    if (Test-Path -LiteralPath $PlanPath) {
        $taskPlan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $taskPlan | Add-Member -NotePropertyName maintenance_error -NotePropertyValue $taskMessage -Force
        Save-RelayPlan $taskPlan
        Copy-Item -LiteralPath $PlanPath -Destination (Join-Path $taskReportDirectory 'last-upgrade.json') -Force
    }
    Write-Output $taskMessage
    exit 1
} finally { $taskRegistry.Dispose() }
