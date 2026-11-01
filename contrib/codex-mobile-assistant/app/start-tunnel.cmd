@echo off
call "%~dp0start.cmd" --tunnel %*
exit /b %errorlevel%
