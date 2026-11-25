#!/bin/bash
# 在虚拟显示器里跑 Linux 的端到端测试：run_linux.sh [输出目录]
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-/tmp/em_run}
mkdir -p "$OUT" && cd "$OUT" || exit 2
export DISPLAY=:99 XDG_CACHE_HOME="$OUT/xdg" PULSE_SERVER=unix:/nonexistent
rm -rf e2e xdg
Xvfb :99 -screen 0 1100x520x24 >/dev/null 2>&1 &
XV=$!
sleep 1
timeout 170 /usr/bin/python3 "$HERE/e2e.py" /usr/bin/python3 "$HERE/../desktop/speak.py"
RC=$?
kill $XV 2>/dev/null
exit $RC
