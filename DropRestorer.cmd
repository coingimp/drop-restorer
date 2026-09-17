@echo off
if not exist "%~dp0.venv-drop-restorer\Scripts\pythonw.exe" (
  echo DropRestorer environment is missing. Run Install-DropRestorer.ps1 first.
  pause
  exit /b 1
)
start "" "%~dp0.venv-drop-restorer\Scripts\pythonw.exe" "%~dp0DropRestorer.pyw"
