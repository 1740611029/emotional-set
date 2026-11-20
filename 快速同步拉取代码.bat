@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   emotional-set  代码快速同步（gh-proxy 加速拉取）
echo   走 gh-proxy.com 镜像通道，国内拉取更快
echo ============================================
echo.

REM ==========================================================================
REM 前置自检：工作区有未提交改动时，git merge --ff-only 必失败，先中止并提示。
REM
REM 本文件有三个硬约束，改动时务必遵守：
REM
REM 约束一：文件必须存为 GBK 编码（代码页 936），且不要加 chcp 65001。
REM         UTF-8 加 chcp 65001 会让 cmd 读文件时字节偏移错位，
REM         表现为部分中文行被截断后当成命令执行，报 is not recognized。
REM         本仓库其它 bat 文件同样用 GBK。
REM
REM 约束二：自检提示不要放进 if 括号块，块内全角括号会错切代码块。
REM         一律用 goto 跳转。
REM
REM 约束三：不要用 find /c /v 空模式 计数，无控制台时会挂死等待控制台输入。
REM         用 for /f 逐行累加。
REM ==========================================================================

set "DIRTY=0"
git status --porcelain > "%TEMP%\emotional-set-status.txt" 2>nul
for /f %%A in ('type "%TEMP%\emotional-set-status.txt"') do set /a DIRTY+=1
del "%TEMP%\emotional-set-status.txt" >nul 2>&1

if "%DIRTY%"=="0" goto CLEAN

echo [中止] 工作区存在 %DIRTY% 个未提交改动，例如 data\etf-volume-events.json 被运行时刷新。
echo        先提交或暂存后再同步：
echo          git stash
echo          git add -A ^&^& git commit -m "..."
echo        否则 git merge --ff-only 会直接失败。
echo.
echo        查看明细：git status --porcelain
pause
exit /b 1

:CLEAN
echo [0/3] 前置自检通过：工作区干净。

git remote get-url fast >nul 2>&1
if errorlevel 1 (
    git remote add fast https://gh-proxy.com/https://github.com/1740611029/emotional-set.git
    echo [init] 已添加加速远端 fast
)

echo.
echo [1/3] 从加速通道拉取 main ...
git fetch fast main --progress
if errorlevel 1 (
    echo.
    echo [失败] 加速通道波动或不可达，可改用 git pull origin main 直连，或重跑本脚本。
    pause
    exit /b 1
)

echo.
echo [2/3] 快进合并到本地 main ...
git merge --ff-only fast/main
if errorlevel 1 (
    echo.
    echo [失败] 本地有未提交改动，无法快进合并。
    echo        请先 git stash 或 git commit，再重新运行本脚本。
    pause
    exit /b 1
)

echo.
echo [3/3] 同步 origin/main 指针 ...
for /f %%i in ('git rev-parse fast/main') do git update-ref refs/remotes/origin/main %%i

echo.
echo ----- 当前最近 3 次提交 -----
git log --oneline -3
echo.
echo 同步完成！
pause
