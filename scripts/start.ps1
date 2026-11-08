# Run from PowerShell after completing the README setup.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $projectPython)) { throw "Create .venv using the README setup first." }
& $projectPython -m app.cli migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $projectPython -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
exit $LASTEXITCODE

