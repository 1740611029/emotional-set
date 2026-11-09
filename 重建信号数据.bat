@echo off
setlocal
cd /d "%~dp0"

REM Rebuild signal JSON from raw daily data.
REM Optional switch: fetch the daily bars that lag the last completed trading day
REM first, then rebuild. Both modes rebuild the two JSON files.
REM
REM Usage:
REM   rebuild.bat                  interactive menu (press Enter = rebuild only)
REM   rebuild.bat fetch            fetch missing bars, then rebuild
REM   rebuild.bat fetch --force    ignore the "already backfilled today" mark
REM   rebuild.bat rebuild          rebuild only, no network
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

REM ---- switch: rebuild only, or fetch missing daily bars first ----
set "MODE="
set "FORCE="
for %%A in (%1 %2) do (
  if /i "%%~A"=="fetch" set "MODE=fetch"
  if /i "%%~A"=="--fetch" set "MODE=fetch"
  if /i "%%~A"=="/f" set "MODE=fetch"
  if /i "%%~A"=="rebuild" set "MODE=rebuild"
  if /i "%%~A"=="--rebuild-only" set "MODE=rebuild"
  if /i "%%~A"=="--force" set "FORCE=--force"
)
if defined MODE goto :dispatch

echo.
echo   [1] Rebuild only      - rebuild JSON from the raw CSV files already on disk
echo   [2] Fetch data first  - pull the missing daily bars, then rebuild
echo.
:ask
set "CHOICE="
set /p CHOICE=Choose 1 or 2, then press Enter (default 1): 
if not defined CHOICE set "CHOICE=1"
set "MODE="
if "%CHOICE%"=="1" set "MODE=rebuild"
if "%CHOICE%"=="2" set "MODE=fetch"
if defined MODE goto :dispatch
echo Please type 1 or 2.
goto :ask

:dispatch
if "%MODE%"=="fetch" goto :fetch_first

echo.
echo Mode: rebuild only, from the raw CSV files already on disk.
echo Building... this can take from a few seconds to a minute.
echo.

%PY% pipeline\build_dashboard.py
if errorlevel 1 goto :failed

echo.
echo [OK] Rebuilt: data\index-signals.json  and  data\sector-trends.json
echo A running service picks up the new files within 30 seconds; no restart needed.
goto :end

:fetch_first
echo.
echo Mode: fetch missing daily bars first, then rebuild.
echo Only data lines that lag the last completed trading day are pulled; EM sector
echo bars are usually final right after the close, while index bars catch up later in
echo the evening, so the two products may differ by 1 trading day.
echo A full sector refresh takes about 10-30 minutes. Please keep this window open.
echo.
%PY% pipeline\refresh_and_rebuild.py %FORCE%
if errorlevel 1 goto :failed
echo.
echo [OK] Data refreshed and rebuilt: data\index-signals.json  and  data\sector-trends.json
echo A running service picks up the new files within 30 seconds; no restart needed.
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
