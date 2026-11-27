#!/bin/bash
# 在虚拟显示器里跑 Linux 的端到端测试：run_linux.sh [输出目录]
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-/tmp/em_run}
mkdir -p "$OUT" && cd "$OUT" || exit 2
# 用另一个端口：这台机器上可能正开着一个真在用的 earImmersion，测试不能把它关掉
export DISPLAY=:99 XDG_CACHE_HOME="$OUT/xdg" PULSE_SERVER=unix:/nonexistent EARIMMERSION_PORT=47641
rm -rf e2e xdg
# 不出声：没有 PulseAudio 可连，ALSA 的默认设备换成"空设备"（声音被直接丢掉，所以播放用时这一项在这里核对不了）
printf 'pcm.!default { type null }\n' > asound.conf
export ALSA_CONFIG_PATH="$OUT/asound.conf"
Xvfb :99 -screen 0 1100x800x24 >/dev/null 2>&1 &
XV=$!
sleep 1
timeout 260 /usr/bin/python3 "$HERE/e2e.py" /usr/bin/python3 "$HERE/../desktop/speak.py"
RC=$?
kill $XV 2>/dev/null
exit $RC
