#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

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
  echo "[ERROR] Python 3.10+ not found."
  exit 1
}

# 用法：./rebuild.sh                  仅用磁盘现有 CSV 重建
#       ./rebuild.sh fetch [--force]  先补落后的日线（--force 忽略“当日已补齐”标记）再重建
MODE="${1:-rebuild}"
case "$MODE" in
  fetch|--fetch|-f)
    shift
    exec "$PY" pipeline/refresh_and_rebuild.py "$@"
    ;;
  rebuild|--rebuild-only)
    ;;
  *)
    echo "[ERROR] unknown mode: $MODE (expected 'rebuild' or 'fetch')"
    exit 1
    ;;
esac

echo "============================================================"
echo "  Rebuild signals from raw daily data"
echo "============================================================"
"$PY" -c "import sys; sys.path.insert(0, 'pipeline'); from confirmed_index_signals import ACTIVE_ALGORITHM, ALGORITHM_VERSION; print('Active algorithm: ' + ACTIVE_ALGORITHM + '  (' + ALGORITHM_VERSION + ')')"
echo
echo "Building..."
echo
exec "$PY" pipeline/build_dashboard.py
