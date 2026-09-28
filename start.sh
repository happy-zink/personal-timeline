#!/usr/bin/env bash
# 私人数据时光轴 - 一键启动
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8764}"

if [[ -n "${MIMO_PYTHON:-}" ]]; then
  PY="$MIMO_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[错误] 未找到 Python" >&2
  exit 1
fi

echo "=========================================="
echo "  私人数据时光轴"
echo "  默认地址: http://127.0.0.1:$PORT"
echo "=========================================="
exec "$PY" server.py --port "$PORT"
