@echo off
setlocal
pushd "%~dp0"
call py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1
if not errorlevel 1 (
    call py -3 -B "%~dp0stop.py" %*
) else (
    call python -B "%~dp0stop.py" %*
)
set "result=%errorlevel%"
popd
if not "%result%"=="0" pause
exit /b %result%
