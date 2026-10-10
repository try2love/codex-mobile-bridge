@echo off
setlocal
for /f "tokens=1,*" %%A in ('type "%~dp0.git"') do set "GIT_DIR=%%B"
set "GIT_WORK_TREE=%~dp0"
git -c safe.directory="%~dp0." %*
