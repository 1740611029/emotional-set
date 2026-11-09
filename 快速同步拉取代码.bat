@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   emotional-set  代码快速同步（gh-proxy 加速拉取）
echo   走 gh-proxy.com 镜像通道，国内拉取更快
echo ============================================
echo.

git remote get-url fast >nul 2>&1
if errorlevel 1 (
    git remote add fast https://gh-proxy.com/https://github.com/1740611029/emotional-set.git
    echo [init] 已添加加速远端 fast
)

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
