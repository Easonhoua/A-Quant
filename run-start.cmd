@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "run_exit=%errorlevel%"
echo.
echo Server stopped. Exit code: %run_exit%
pause
exit /b %run_exit%
