@echo off
setlocal
cd /d "%~dp0"

REM This project requires Python 3.10+. The "python" on PATH may be Python 2,
REM so we must pick a Python 3 interpreter explicitly.
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

echo Interpreter: %PY%
%PY% start.py
goto :end

:no_python
echo.
echo [ERROR] Python 3.10+ not found.
echo The "python" on your PATH may be Python 2, which this project does not support.
echo.
echo Fix:
echo   1. Install Python 3.10+ and check "Add python.exe to PATH"
echo   2. Or run in this folder:
echo        py -3 -m venv .venv
echo        .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.

:end
pause
