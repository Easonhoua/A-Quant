@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Run start.ps1 -Install first.
  pause
  exit /b 1
)
echo Syncing Tencent daily data. Keep this window open until finished.
echo Full-market download may take several hours.
"%~dp0.venv\Scripts\python.exe" -X utf8 -m backend.cli sync --provider tencent
set "run_exit=%errorlevel%"
echo.
if "%run_exit%"=="0" (
  echo Sync finished. Check complete=true, errors=0 and as_of before refreshing analysis.
) else (
  echo Sync failed. Review the error above.
)
pause
exit /b %run_exit%
