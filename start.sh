#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# 本项目只支持 Python 3.10+；优先复用项目虚拟环境
pick_python() {
  for candidate in ".venv/bin/python" "python3" "python"; do
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  echo "[错误] 未找到 Python 3.10 及以上版本。"
  echo "请安装 Python 3.10+，或先执行：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
}

echo "使用解释器: $PY"
exec "$PY" start.py
