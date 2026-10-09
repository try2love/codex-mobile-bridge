@echo off
setlocal DisableDelayedExpansion
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-preview3-windows.ps1"
if errorlevel 1 (
  echo Preview launch failed. See the error above.
  pause
  exit /b 1
)
