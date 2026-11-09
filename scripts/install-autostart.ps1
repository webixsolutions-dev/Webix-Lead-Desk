# Run locally as Administrator after setup and migration. Never paste credentials into chat.
param([switch]$ValidateOnly, [switch]$Uninstall)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $projectPython)) { throw "Complete the local .venv setup first." }
$names = @("Webix Lead Desk - Web", "Webix Lead Desk - Worker")
if ($ValidateOnly) {
    foreach ($script in @("start.ps1", "worker.ps1")) {
        $tokens = $null
        $parseErrors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            (Join-Path $PSScriptRoot $script), [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count -gt 0) { throw "Startup script syntax validation failed." }
    }
    Write-Output "Startup scripts validated; no tasks or credentials created."
    exit 0
}
if ($Uninstall) {
    foreach ($name in $names) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
        }
    }
    exit 0
}
if (-not (Test-Path (Join-Path $projectRoot ".env"))) { throw "Complete .env locally first." }
& $projectPython -m app.cli migrate
if ($LASTEXITCODE -ne 0) { throw "Database migration failed; no tasks installed." }
# Password logon is required for network access while signed out. S4U is unsuitable.
# Credentials are entered in Windows, passed in process memory and stored by Task Scheduler.
$credential = Get-Credential -UserName ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -Message "Windows account for Webix startup tasks (not a mailbox or LinkedIn password)"
if ($null -eq $credential) { throw "Windows credential entry cancelled." }
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$trigger = New-ScheduledTaskTrigger -AtStartup
$scripts = @("start.ps1", "worker.ps1")
for ($index = 0; $index -lt $names.Count; $index++) {
    $scriptPath = Join-Path $PSScriptRoot $scripts[$index]
    $arguments = '-NoProfile -NonInteractive -File "' + $scriptPath + '"'
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments `
        -WorkingDirectory $projectRoot
    $null = Register-ScheduledTask -TaskName $names[$index] -Action $action -Trigger $trigger `
        -Settings $settings -User $credential.UserName `
        -Password $credential.GetNetworkCredential().Password -Force
}
$credential = $null
Write-Output "Startup tasks installed. Start them in Task Scheduler or restart Windows."
