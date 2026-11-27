"""一键启动：接管端口 → 检查依赖 → 必要时构建前端 → 启动数据服务。

用法：
    python start.py               # 默认 127.0.0.1:8000
    PORT=8080 python start.py     # 指定端口
    python start.py --restart     # 兼容旧用法，等同默认（启动前结束占用者）
    python start.py --no-kill     # 安全模式：端口被占用时只提示，不结束占用进程
    python start.py --no-open     # 不自动打开浏览器（脚本 / 远程调用）

端口被占用（上一次的实例没关、或别的程序占着）时，默认先结束占用进程再启动，
不再报 WinError 10048；系统关键进程不会被结束。同一端口另用文件锁互斥，
挡住「上一个实例还在启动、尚未监听端口」的抢端口窗口。
监听 socket 由本脚本自己 bind（带 SO_REUSEADDR），避免刚退出实例的连接残留
在 Windows 上再次触发 10048。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
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
RUNTIME = ROOT / "data" / "runtime"

REQUIRED = (
    "pandas",
    "numpy",
    "requests",
    "jsonschema",
    "akshare",
    "fastapi",
    "uvicorn",
)

# 结束占用者时的保护名单：系统关键进程一律不碰
PROTECTED_NAMES = {
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "explorer.exe",
    "dwm.exe",
    "taskhostw.exe",
}


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


def _url(host: str, port: int, path: str = "/") -> str:
    return f"http://{host}:{port}{path}"


def probe_running(host: str, port: int) -> dict | None:
    """端口上已有本项目实例时返回 /api/health 内容，否则返回 None。

    以 health 的 `alive` 字段识别本项目：端口空闲、被别的程序监听、
    或是本项目的实例但尚未就绪，都会得到 None。
    """
    url = _url(host, port, "/api/health")
    for _ in range(2):
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("alive") is True:
            return data
        return None
    return None


def listener_pid(port: int) -> int | None:
    """用 netstat 找监听该端口的进程 PID。

    只看 LISTENING：TCP 连接残留（TIME_WAIT/CLOSE_WAIT）不算占用，
    否则刚结束一个实例时会误判成「端口仍被占」。
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if (
            len(parts) >= 5
            and parts[0].upper() == "TCP"
            and parts[3].upper() == "LISTENING"
        ):
            if parts[1].endswith(f":{port}"):
                try:
                    return int(parts[4])
                except ValueError:
                    return None
    return None


def proc_info(pid: int) -> dict:
    """取进程名与命令行，仅用于结束前展示；取不到返回空字典。"""
    ps = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}';"
        "if($p){$p.Name + [char]9 + $p.CommandLine}"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout
    except Exception:
        return {}
    text = out.strip()
    if not text:
        return {}
    name, _, cmd = text.partition("\t")
    return {"name": name.strip(), "cmd": cmd.strip()}


def pid_alive(pid: int) -> bool:
    try:
        res = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Get-Process -Id {pid} -ErrorAction SilentlyContinue",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return False
    return res.returncode == 0


def kill_tree(pid: int) -> bool:
    try:
        res = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return False
    return res.returncode == 0


def wait_port_free(port: int, timeout: float = 15.0) -> bool:
    """等到端口没有监听进程（连接残留不算占用）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if listener_pid(port) is None:
            return True
        time.sleep(0.25)
    return listener_pid(port) is None


def lock_path(port: int) -> Path:
    return RUNTIME / f"server-{port}.lock"


def lock_owner(port: int) -> int | None:
    """读锁文件里记录的 PID（启动中的实例），读不到返回 None。"""
    try:
        text = lock_path(port).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for token in text.split():
        try:
            return int(token)
        except ValueError:
            continue
    return None


def try_lock(port: int) -> tuple[bool, int | None]:
    """同端口互斥的文件锁，返回 (是否拿到锁, fd)。

    挡的是「上一个实例还在启动、尚未监听端口」的窗口：这时 health 探测看不出
    冲突，端口也还没被占，uvicorn 要到 bind 时才报 10048。锁随进程退出由系统
    释放（Windows msvcrt / POSIX flock），不会留下需要手工清理的陈旧锁。
    锁文件不可创建时不阻塞启动，仍按端口探测与 uvicorn 报错兜底。
    """
    try:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path(port), os.O_CREAT | os.O_RDWR)
    except OSError:
        return True, None

    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415  # 仅 Windows

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415  # 仅 POSIX

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False, None

    try:
        os.truncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    except OSError:
        pass
    return True, fd


def bind_listen_socket(host: str, port: int) -> socket.socket | None:
    """自己 bind 监听端口，交给 uvicorn 使用。

    Windows 上 asyncio 默认不设 SO_REUSEADDR，刚退出的实例留下连接残留时，
    uvicorn 直接 bind 会报 10048；这里先占住端口，重启不再受残留影响。
    只在没有监听进程时调用；bind 成功后再确认没人抢先监听。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        return None
    if listener_pid(port) is not None:
        sock.close()
        return None
    return sock


def _describe(pid: int) -> str:
    info = proc_info(pid)
    name = info.get("name") or "未知进程"
    cmd = info.get("cmd")
    if cmd:
        return f"{name}（PID {pid}）\n    {cmd[:180]}"
    return f"{name}（PID {pid}）"


def clear_port(port: int, *, allow_kill: bool) -> bool:
    """端口有监听者时结束它；返回端口是否已腾出。"""
    pid = listener_pid(port)
    if pid is None:
        return True

    info = proc_info(pid)
    name = (info.get("name") or "").lower()
    if name in PROTECTED_NAMES:
        print(f"[错误] 端口 {port} 由系统关键进程 {_describe(pid)} 占用，未结束它。")
        return False

    if not allow_kill:
        print(f"[提示] 端口 {port} 被 {_describe(pid)} 占用；--no-kill 下未结束它。")
        return False

    print(f"  结束占用端口 {port} 的进程：{_describe(pid)}")
    if not kill_tree(pid):
        print(f"[错误] 结束 PID {pid} 失败（可能权限不足）。")
        return False
    if wait_port_free(port):
        print(f"  端口 {port} 已释放。")
        return True
    print(f"[错误] 结束 PID {pid} 后端口 {port} 仍被占用。")
    return False


def acquire_lock(port: int, *, allow_kill: bool) -> tuple[int | None, bool]:
    """拿到同端口启动锁；必要时结束仍持锁的启动中实例。

    返回 (fd, verdict)：fd 为 None 时，verdict=True 表示「有别的实例在处理，
    稍后可访问」，False 表示启动失败。
    """
    ok, fd = try_lock(port)
    if ok:
        return fd, True

    owner = lock_owner(port)
    if allow_kill and owner and owner != os.getpid() and pid_alive(owner):
        print(f"  端口 {port} 上有实例正在启动（PID {owner}），先结束它。")
        kill_tree(owner)
        for _ in range(20):
            time.sleep(0.25)
            ok, fd = try_lock(port)
            if ok:
                return fd, True
        print(f"[错误] 结束 PID {owner} 后仍拿不到端口 {port} 的启动锁。")
        return None, False

    print("=" * 58)
    print(f"  端口 {port} 上已有实例正在启动（{_url('127.0.0.1', port)}）")
    print("  稍等几秒再打开页面即可；--no-kill 下不会结束它。")
    print("=" * 58)
    return None, True


def _print_banner(host: str, port: int) -> None:
    print("=" * 58)
    print(f"  SignalHub 数据服务  {_url(host, port)}")
    print(f"  解释器 Python {sys.version.split()[0]}（{sys.executable}）")
    print("  启动即检查落后数据并补齐；盘中每 10 分钟复查一次")
    print("  首次启动若缺少原始数据，会先拉取指数与板块日线")
    print("=" * 58)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SignalHub 数据服务一键启动")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="兼容旧用法，等同默认（启动前结束占用者）",
    )
    parser.add_argument(
        "--no-kill",
        dest="no_kill",
        action="store_true",
        help="安全模式：端口被占用时只提示，不结束占用进程",
    )
    parser.add_argument(
        "--no-open", dest="no_open", action="store_true", help="不自动打开浏览器"
    )
    args = parser.parse_args(argv)
    allow_kill = not args.no_kill

    if sys.version_info < (3, 10):
        print(f"需要 Python 3.10+，当前为 {sys.version.split()[0]}")
        return 1

    host = os.environ.get("HOST", "127.0.0.1")
    raw_port = os.environ.get("PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError:
        print(f"[错误] PORT 需为整数，当前为 {raw_port!r}")
        return 1

    running = probe_running(host, port)
    if running:
        print(
            f"  发现已在运行的实例：{_url(host, port)}"
            f"（数据状态 {running.get('data_state')}"
            f"，最近刷新 {running.get('last_refresh_at')}）"
        )

    # 1) 结束端口上的监听者（上一次的实例，或别的程序）
    if not clear_port(port, allow_kill=allow_kill):
        print("=" * 58)
        print(f"[错误] 端口 {port} 未能腾出，无法启动。")
        print("  可换端口启动：set PORT=8001 后重新运行 启动.bat")
        print("=" * 58)
        return 1

    # 2) 端口可能还没监听但已有实例在补齐数据（持锁），同样先结束它
    fd, verdict = acquire_lock(port, allow_kill=allow_kill)
    if fd is None:
        return 0 if verdict else 1

    # 3) 先占住监听口：绕过刚退出实例留下的连接残留（Windows 10048）
    sock = None
    for attempt in range(3):
        sock = bind_listen_socket(host, port)
        if sock is not None:
            break
        if attempt == 0 and listener_pid(port) is not None:
            if not clear_port(port, allow_kill=allow_kill):
                break
        time.sleep(1.0)
    if sock is None:
        print("=" * 58)
        print(f"[错误] 端口 {port} 绑定失败，本次放弃启动。")
        print(f"  查看占用：netstat -ano | findstr :{port}")
        print("  或换端口：set PORT=8001 后重新运行 启动.bat")
        print("=" * 58)
        return 1

    if not check_dependencies():
        sock.close()
        return 1
    ensure_frontend()

    os.environ["HOST"] = host
    os.environ["PORT"] = str(port)

    _print_banner(host, port)

    def _wait_and_open() -> None:
        url = _url(host, port)
        health = _url(host, port, "/api/health")
        # 服务不再在 startup 里阻塞补齐，正常 2-3 秒就绪；留足冷启动与前端构建余量。
        for attempt in range(120):
            try:
                with urllib.request.urlopen(health, timeout=2):
                    webbrowser.open(url)
                    return
            except Exception:
                if attempt and attempt % 15 == 0:
                    print(
                        f"  服务仍在启动，等待就绪后自动打开 {url}（数据补齐在后台进行）",
                        flush=True,
                    )
                time.sleep(1)
        print(f"[WARN] 服务仍未就绪，请稍后手动打开 {url}")

    if not args.no_open:
        threading.Thread(target=_wait_and_open, daemon=True).start()

    import uvicorn

    config = uvicorn.Config("server.app:app", host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    except OSError as exc:
        # 兜底：启动瞬间被别的进程抢端口（例如另一套启动方式）
        if getattr(exc, "errno", None) in (98, 10048):
            print(f"[错误] 端口 {port} 绑定失败（{exc}）。稍后重试，或换 PORT 启动。")
            return 1
        raise
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
