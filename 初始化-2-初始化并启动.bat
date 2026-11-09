@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM First-time setup on a new machine: install Python if missing,
REM create venv, install deps, then launch.
REM Daily use: just run 启动.bat instead.

echo ============================================================
echo   SignalHub 首次初始化（新电脑只需运行这一次）
echo   以后日常使用请双击「启动.bat」
echo ============================================================
echo.

REM ---- Find a Python 3.10+ interpreter ----
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

if not defined PY goto :install_python

echo 找到 Python: %PY%
echo.

REM ---- Create virtual environment ----
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 创建虚拟环境 .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto :failed
) else (
  echo [1/3] 虚拟环境已存在，跳过创建
)

set "VENV_PY=.venv\Scripts\python.exe"

REM ---- Install Python dependencies ----
echo [2/3] 安装 Python 依赖（已安装的会自动跳过）...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

REM ---- Check npm (frontend build happens on first start) ----
echo [3/3] 检查前端环境...
where npm >nul 2>&1
if errorlevel 1 (
  echo.
  echo [警告] 未找到 npm。前端将无法构建，页面打不开。
  echo        请安装 Node.js LTS 后重新运行本脚本：https://nodejs.org/
  echo.
) else (
  echo        npm 已就绪（首次启动时会自动构建前端，需几分钟）
)

echo.
echo ============================================================
echo   初始化完成，正在启动项目……
echo   首次启动会拉取几年历史数据，约 1-2 分钟，请耐心等待
echo ============================================================
echo.
"%VENV_PY%" start.py
goto :end

:install_python
echo.
echo 未检测到 Python 3.10+，准备用根目录自带的安装包安装……
if not exist "初始化-1-安装Python.exe" goto :no_installer
echo 安装过程会显示进度条，无需任何操作，请耐心等待。
echo.
start "" /wait "初始化-1-安装Python.exe" /passive InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
echo.
echo ============================================================
echo   Python 安装完成！
echo   请【重新双击】本脚本，继续剩下的初始化步骤。
echo   （新装的 Python 需要重新打开窗口才能被识别）
echo ============================================================
goto :end

:no_installer
echo.
echo [错误] 未找到安装包「初始化-1-安装Python.exe」。
echo 请从 https://www.python.org/downloads/ 下载 Python 3.10+ 安装，
echo 安装时务必勾选 "Add python.exe to PATH"，然后重新运行本脚本。
echo.
goto :end

:failed
echo.
echo [失败] 初始化未完成，请把上面的错误信息发出来排查。
echo.

:end
pause
