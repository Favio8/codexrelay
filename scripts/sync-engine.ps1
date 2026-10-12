$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSite = Join-Path $taskRoot 'runtime\Lib\site-packages'
if (-not (Test-Path -LiteralPath $taskSite)) { throw 'Prepare the runtime first' }
Copy-Item -LiteralPath (Join-Path $taskRoot 'engine\codexrelay') -Destination $taskSite -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'vendor\deepseek-as-subagent\src\deepseek_mcp') -Destination $taskSite -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'LICENSE') -Destination (Join-Path $taskRoot 'runtime\CODEXRELAY-LICENSE.txt') -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'vendor\deepseek-as-subagent\LICENSE') -Destination (Join-Path $taskRoot 'runtime\DEEPSEEK-LICENSE.txt') -Force
