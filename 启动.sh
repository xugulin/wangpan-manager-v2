#!/bin/bash
# 网盘管理 V2 启动脚本（Linux）—— 用项目自带的解释器
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
for PY in "运行环境/venv/bin/python" "运行环境/venv/bin/python3" python3; do
  if command -v "$PY" >/dev/null 2>&1 || [ -x "$PY" ]; then
    exec "$PY" 启动.py "$@"
  fi
done
echo "找不到可用的 Python 解释器"
exit 1
