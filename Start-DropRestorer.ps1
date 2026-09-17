param([string]$Project = '', [int]$Port = 8780, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv-drop-restorer\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'DropRestorer environment is missing. Run Install-DropRestorer.ps1 first.'
}
$taskArguments = @('-X', 'utf8', '-m', 'drop_restorer.web.launcher', '--port', "$Port")
if ($NoBrowser) { $taskArguments += '--no-browser' }
if ($Project) {
    $taskProjectPath = (Resolve-Path -LiteralPath $Project).Path
    $taskArguments += @('--project', ('"' + $taskProjectPath + '"'))
}
Start-Process -FilePath $taskPython -ArgumentList $taskArguments -WorkingDirectory $taskRoot -WindowStyle Hidden
