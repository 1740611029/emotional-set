@echo off
setlocal
cd /d "%~dp0"

REM Rebuild signal JSON from raw daily data.
REM Every run first refreshes the daily bars that lag the last completed trading
REM day (no-op when already fresh), then rebuilds the front-end JSON files.
REM
REM Usage:
REM   重建信号数据.bat            refresh missing bars, then rebuild (default)
REM   重建信号数据.bat --force    ignore the "already backfilled today" mark
REM
REM Requires Python 3.10+; the "python" on PATH may be Python 2.
set "PY="

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=.venv\Scripts\python.exe"
)

if not defined PY (
  py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)

if not defined PY (
  python3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python3"
)

if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY goto :no_python

echo ============================================================
echo   Rebuild signals from raw daily data
echo ============================================================
echo.

%PY% -c "import sys; sys.path.insert(0,'pipeline'); from confirmed_index_signals import ACTIVE_ALGORITHM, ALGORITHM_VERSION; print('Active algorithm: ' + ACTIVE_ALGORITHM + '  (' + ALGORITHM_VERSION + ')')"
if errorlevel 1 goto :failed

REM ---- always: refresh missing daily bars first, then rebuild ----
REM --force skips the "already backfilled today" mark; a plain run is enough daily.
set "FORCE="
for %%A in (%1 %2) do (
  if /i "%%~A"=="--force" set "FORCE=--force"
  if /i "%%~A"=="/f" set "FORCE=--force"
)

echo.
echo Mode: refresh missing daily bars first, then rebuild.
echo Only data lines that lag the last completed trading day are pulled; THS
echo industry bars are usually final right after the close, while index bars
echo catch up later in the evening, so the two products may differ by 1 trading day.
echo A full sector refresh takes about 10-30 minutes. Please keep this window open.
echo.

%PY% pipeline\refresh_and_rebuild.py %FORCE%
if errorlevel 1 goto :failed

echo.
echo [OK] Data refreshed and rebuilt: data\index-signals.json + data\sector-trends.json + data\etf-volume-events.json
echo A running service picks up the new files within 30 seconds; no restart needed.
echo.
REM 输出三份产物各自的 as_of 与相对最近已完成交易日的落后交易日数（不联网）。
REM as_of 只由原始 CSV 尾日决定，不因生成时间更新而显示为最新。
%PY% pipeline\report_asof.py
goto :end

:failed
echo.
echo [FAILED] Rebuild did not complete.
echo Existing data files were kept unchanged (validation fails before writing).
echo Check the error output above.

:end
echo.
pause
goto :eof

:no_python
echo.
echo [ERROR] Python 3.10+ not found.
echo The "python" on your PATH may be Python 2, which this project does not support.
echo Fix: install Python 3.10+ and check "Add python.exe to PATH",
echo or run: py -3 -m venv .venv
echo.
pause
