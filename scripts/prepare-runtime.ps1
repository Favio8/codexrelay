param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$DependencyPython = $Python
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskRuntime = Join-Path $taskRoot 'runtime'
$taskBase = (& $Python -I -c 'import sys; print(sys.base_prefix)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Python runtime' }
if (-not (Test-Path -LiteralPath (Join-Path $taskBase 'python312.dll'))) { throw 'Use a standalone Python 3.12 distribution' }
New-Item -ItemType Directory -Force -Path $taskRuntime | Out-Null
foreach ($taskName in @('DLLs','Lib','libs','include','LICENSE.txt','python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll','vcruntime140_1.dll')) {
    $taskSource = Join-Path $taskBase $taskName
    if (Test-Path -LiteralPath $taskSource) { Copy-Item -LiteralPath $taskSource -Destination $taskRuntime -Recurse -Force }
}
$taskSite = Join-Path $taskRuntime 'Lib\site-packages'
& $DependencyPython -m pip install --disable-pip-version-check --require-hashes -r (Join-Path $taskRoot 'vendor\deepseek-as-subagent\requirements.lock') --target $taskSite --upgrade
if ($LASTEXITCODE -ne 0) { throw 'Locked runtime dependencies could not be installed' }
Copy-Item -LiteralPath (Join-Path $taskRoot 'vendor\deepseek-as-subagent\src\deepseek_mcp') -Destination $taskSite -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'engine\codexrelay') -Destination $taskSite -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'vendor\deepseek-as-subagent\LICENSE') -Destination (Join-Path $taskRuntime 'DEEPSEEK-LICENSE.txt') -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'LICENSE') -Destination (Join-Path $taskRuntime 'CODEXRELAY-LICENSE.txt') -Force
& (Join-Path $taskRuntime 'python.exe') -I -X utf8 -c 'import codexrelay, deepseek_mcp, mcp, openai, tomlkit; print("Runtime ready", codexrelay.__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Runtime import verification failed' }
