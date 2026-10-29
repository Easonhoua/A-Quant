@echo off
rem Double-click entry: read-only review tool (never touches ledger, never trades).
rem Default runs the snapshot health check. Extra args are forwarded, e.g.:
rem   run-review.cmd study
rem   run-review.cmd refine
rem   run-review.cmd backtest --stocks 100
rem   run-review.cmd snapshot --mode demo
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

set "ARGS=%*"
if "%ARGS%"=="" set "ARGS=snapshot"

if not exist "data\live.json" (
  echo [WARN] data\live.json not found - only demo data is available.
  echo        Fetch live data first:  run-daily.cmd
  echo        Or run on demo data:    run-review.cmd snapshot --mode demo
  echo.
)

echo ============================================================
echo  A-Quant  strategy review tool  (read-only)
echo ------------------------------------------------------------
echo  snapshot     coverage / gaps / what today's report would pick
echo  study        forward returns, 2R race, exit attribution
echo  sensitivity  Donchian window x volume-ratio grid
echo  refine       filter vs ranking experiment
echo  backtest     sampled backtest with the real engine
echo.
echo  Running: %ARGS%
echo ============================================================
echo.

".venv\Scripts\python.exe" -X utf8 -m tools.review %ARGS%
set "run_exit=%errorlevel%"

echo.
echo [exit code %run_exit%]  JSON written under data\review\
echo.
pause
exit /b %run_exit%
