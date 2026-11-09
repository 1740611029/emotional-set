"""一键启动：检查依赖 → 必要时构建前端 → 启动数据服务。

用法：
    python start.py            # 默认 127.0.0.1:8000
    PORT=8080 python start.py  # 指定端口
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "signal-hub-web"
DIST = WEB / "dist"

REQUIRED = ("pandas", "numpy", "requests", "jsonschema", "akshare", "fastapi", "uvicorn")


def check_dependencies() -> bool:
    missing = []
    for name in REQUIRED:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        print("缺少依赖：" + "、".join(missing))
        print("请先执行：pip install -r requirements.txt")
        return False
    return True


def ensure_frontend() -> None:
    if (DIST / "index.html").exists():
        return
    npm = shutil.which("npm")
    if not npm:
        print("警告：未找到 npm，且前端尚未构建，页面将无法访问。")
        print("请在 signal-hub-web 目录执行 npm install && npm run build")
        return
    print("首次运行：正在构建前端……")
    subprocess.run([npm, "install"], cwd=WEB, check=False)
    subprocess.run([npm, "run", "build"], cwd=WEB, check=False)


def main() -> int:
    if sys.version_info < (3, 10):
        print(f"需要 Python 3.10+，当前为 {sys.version.split()[0]}")
        return 1
    if not check_dependencies():
        return 1
    ensure_frontend()

    host = os.environ.get("HOST", "127.0.0.1")
    port = os.environ.get("PORT", "8000")
    os.environ["HOST"] = host
    os.environ["PORT"] = port

    print("=" * 58)
    print(f"  SignalHub 数据服务  http://{host}:{port}")
    print(f"  解释器 Python {sys.version.split()[0]}（{sys.executable}）")
    print("  盘中每 30 秒刷新实时快照；收盘后自动重算日线信号")
    print("  首次启动若缺少原始数据，会先拉取指数与板块日线")
    print("=" * 58)

    def _wait_and_open():
        url = f"http://{host}:{port}/"
        health = f"http://{host}:{port}/api/health"
        for _ in range(60):
            try:
                with urllib.request.urlopen(health, timeout=2):
                    webbrowser.open(url)
                    return
            except Exception:
                time.sleep(1)
        print("[WARN] 服务未就绪，未自动打开浏览器。")

    threading.Thread(target=_wait_and_open, daemon=True).start()

    import uvicorn

    uvicorn.run("server.app:app", host=host, port=int(port), log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
