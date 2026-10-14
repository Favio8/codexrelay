$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSpec = Get-Content -LiteralPath (Join-Path $taskRoot 'vendor/pi-agent/node-distribution.json') -Raw | ConvertFrom-Json
$taskDestination = Join-Path $taskRoot 'runtime/pi'
$taskDownloads = Join-Path $taskRoot '.tmp/downloads'
New-Item -ItemType Directory -Force -Path $taskDestination,$taskDownloads | Out-Null
$taskArchive = Join-Path $taskDownloads ('node-' + $taskSpec.version + '-win-x64.zip')
if (-not (Test-Path -LiteralPath $taskArchive)) { Invoke-WebRequest -Uri $taskSpec.url -OutFile $taskArchive }
if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskSpec.sha256) { throw 'Pinned Node archive hash mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskZip = [IO.Compression.ZipFile]::OpenRead($taskArchive)
try {
    foreach ($taskName in @('node.exe','LICENSE')) {
        $taskEntry = $taskZip.GetEntry('node-' + $taskSpec.version + '-win-x64/' + $taskName)
        if (-not $taskEntry) { throw 'Pinned Node archive is incomplete' }
        $taskOutput = Join-Path $taskDestination $taskName
        $taskStream = $taskEntry.Open()
        $taskHasher = [Security.Cryptography.SHA256]::Create()
        try { $taskExpected = [BitConverter]::ToString($taskHasher.ComputeHash($taskStream)).Replace('-','').ToLowerInvariant() }
        finally { $taskStream.Dispose(); $taskHasher.Dispose() }
        # Keep identical runtime files in place, including while isolated tests use Node.
        if ((Test-Path -LiteralPath $taskOutput) -and (Get-FileHash -LiteralPath $taskOutput -Algorithm SHA256).Hash.ToLowerInvariant() -eq $taskExpected) { continue }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($taskEntry,$taskOutput,$true)
    }
} finally { $taskZip.Dispose() }
Push-Location $taskRoot
try {
    npm ci --prefix vendor/pi-agent --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Locked Pi dependencies could not be installed' }
    node scripts/build-pi-runtime.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Pi runtime bundle failed' }
} finally { Pop-Location }
