@echo off
setlocal
set "CMB_DATA_DIR=%~dp0.local\windows-preview3-user"
set "CMB_UPDATE_DATA_DIR="
set "CMB_UPDATE_TRANSACTION="
set "ELECTRON_RUN_AS_NODE="
set "CMB_APP=%~dp0dist\desktop\win-unpacked\Codex Mobile Bridge.exe"
if not exist "%CMB_APP%" (
  echo Build the Windows app first. See docs\windows-preview3-local.md.
  pause
  exit /b 1
)
start "" "%CMB_APP%"
