@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM 开启追踪 = 调用已有的注册脚本，重新注册 Windows 计划任务
REM 注册脚本内含触发规则：工作日 09:00-15:30 每 1 分钟、其余时段每 30 分钟

echo 正在注册计划任务「同花顺发文追踪-全能的野人」……
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "初始化-2-注册同花顺发文追踪任务.ps1"
echo.
pause
