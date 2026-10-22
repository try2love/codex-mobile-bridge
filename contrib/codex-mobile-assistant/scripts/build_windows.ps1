$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install requirements-build.txt first; see README.md.'
}
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath scripts/fetch_cloudflared.py
    if ($LASTEXITCODE -ne 0) { throw 'cloudflared preparation failed' }
    $specPath = (Get-ChildItem -LiteralPath $projectRoot -Filter '*.spec').FullName
    & $pythonPath -m PyInstaller --noconfirm --distpath dist --workpath build $specPath
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
    Write-Host 'Built dist/CodexMobileAssistant.exe'
} finally {
    Pop-Location
}
