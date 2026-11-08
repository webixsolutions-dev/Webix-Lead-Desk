$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $projectPython)) { throw "Create .venv using the README setup first." }
& $projectPython -m app.cli worker
exit $LASTEXITCODE

