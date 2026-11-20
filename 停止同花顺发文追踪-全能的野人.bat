@echo off
chcp 65001 >nul
setlocal
set "TASK=同花顺发文追踪-全能的野人"

REM 停止追踪 = 删除已注册的 Windows 计划任务
REM 先尝试结束正在运行中的实例（若有），再删除注册

schtasks /End /TN "%TASK%" >nul 2>&1
schtasks /Delete /TN "%TASK%" /F >nul 2>&1

if errorlevel 1 (
  echo 未找到计划任务「%TASK%」，可能之前已经停止过。
) else (
  echo 已删除计划任务「%TASK%」，发文追踪已停止。
  echo 如需重新开启，请双击「开启同花顺发文追踪-全能的野人.bat」。
)
echo.
pause
