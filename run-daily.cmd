@echo off
rem Double-click entry: full-market close sync + holdings-first daily report.
rem Wrapper for daily.ps1. Requires .venv and requirements-live.txt (AKShare).
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python environment missing.
  echo         Run run-start.cmd once ^(it installs .venv^), then try again.
  pause
  exit /b 1
)

set "CAPITAL=%~1"
if "%CAPITAL%"=="" set "CAPITAL=100000"

echo ============================================================
echo  A-Quant  close sync  +  daily report
echo ------------------------------------------------------------
echo  Account equity : %CAPITAL%
echo  Output         : data\report-live.json
echo.
echo  Full-market sync can take a long time. Keep this window open.
echo  Closing it does NOT damage the ledger (data\state.sqlite3).
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0daily.ps1" -Capital %CAPITAL%
set "run_exit=%errorlevel%"

echo.
if "%run_exit%"=="0" (
  echo [OK] Done. Open or refresh http://127.0.0.1:8765 ,
  echo      switch data to "AKShare local cache", then click "Re-analyze".
) else (
  echo [FAILED] exit code %run_exit%.
  echo          Read the message above. Do not trade from an old report.
  echo          If AKShare is missing:  .venv\Scripts\python.exe -m pip install -r requirements-live.txt
)
echo.
pause
exit /b %run_exit%
