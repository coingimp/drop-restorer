param(
    [ValidateRange(1024, 65535)][int]$Port = 8780,
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$taskPython = Join-Path $taskRoot '.venv-drop-restorer\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
    throw 'DropRestorer environment is missing. Run Install-DropRestorer.ps1 first.'
}
$taskIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$taskOwner = $taskIdentity.Name
$taskOwnerSid = $taskIdentity.User.Value
$taskHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $taskHash = $taskHasher.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($taskRoot.ToLowerInvariant()))
} finally {
    $taskHasher.Dispose()
}
$taskSuffix = ([System.BitConverter]::ToString($taskHash)).Replace('-', '').Substring(0, 12).ToLowerInvariant()
$taskName = "DropRestorer-$Port-$taskSuffix"
$taskArguments = "-X utf8 -u -m drop_restorer.web --port $Port --background"
$taskPlan = [ordered]@{
    taskName = $taskName
    workspace = $taskRoot
    port = $Port
    url = "http://127.0.0.1:$Port/"
    user = $taskOwner
    userSid = $taskOwnerSid
    executable = $taskPython
    arguments = $taskArguments
    startup = 'Current user logon, plus every minute while logged on'
    recovery = 'Restart after exit; running instances are kept'
}
if ($PlanOnly) {
    $taskPlan | ConvertTo-Json
    return
}
if ($taskOwner -match 'sandbox') {
    throw 'Run this installer as the Windows desktop user, outside the sandbox.'
}

# The service is owned by Task Scheduler, independent of PowerShell and Codex.
# Repetition also covers successful exits; RestartCount alone covers only errors.
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument $taskArguments -WorkingDirectory $taskRoot
$taskLogon = New-ScheduledTaskTrigger -AtLogOn -User $taskOwnerSid
$taskRecovery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskOwnerSid -LogonType Interactive -RunLevel Limited
$taskDefinition = New-ScheduledTask -Action $taskAction -Trigger @($taskLogon, $taskRecovery) `
    -Settings $taskSettings -Principal $taskPrincipal -Description "DropRestorer local panel: $taskRoot (port $Port)."
Register-ScheduledTask -TaskName $taskName -InputObject $taskDefinition -Force | Out-Null

$taskOutput = Join-Path $taskRoot 'var\drop-restorer'
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskPlan | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskOutput "web-autostart-$Port.json") -Encoding UTF8
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
