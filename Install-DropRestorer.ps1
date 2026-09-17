$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskEnvironment = Join-Path $taskRoot '.venv-drop-restorer'
if (-not (Test-Path -LiteralPath (Join-Path $taskEnvironment 'Scripts\python.exe'))) {
    python -m venv $taskEnvironment
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& (Join-Path $taskEnvironment 'Scripts\python.exe') -m pip install -e $taskRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not install DropRestorer dependencies.' }
