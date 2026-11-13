#!/bin/sh
# 接力总开关（双击运行；可以拷到桌面用，那样要把下面的 ROOT 改成接力文件夹的完整路径）。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOLDERS="$HOME/.claude/relay-holders.json"
if [ ! -f "$ROOT/bin/relay" ]; then
  ANS=$(/usr/bin/osascript -e 'button returned of (display dialog "没找到接力程序所在的盘，接力程序在那块盘上。最好先插上那块盘，再双击一次。\n\n如果现在急着让 Claude 在内置盘的工程里干活、却被接力拦着，可以点“临时关掉拦截”。插回那块盘后，下次运行接力时会按盘上的状态自动恢复。" with title "AI 接力" buttons {"取消", "临时关掉拦截"} default button "取消")' 2>/dev/null)
  if [ "$ANS" = "临时关掉拦截" ]; then
    /usr/bin/python3 - "$HOLDERS" <<'EOF'
import json, os, sys, tempfile, time
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
except Exception:
    data = {"seq": 0, "projects": {}}
data["live_enabled"] = False
data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
data["note"] = "盘不在时由总开关临时关掉"
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".relay-holders.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, path)
EOF
    if [ "$?" = "0" ]; then
      /usr/bin/osascript -e 'display dialog "已临时关掉拦截。" with title "AI 接力" buttons {"好"} default button 1' >/dev/null 2>&1
    else
      /usr/bin/osascript -e 'display dialog "没关成（内置盘上的抄本写不进去）。请把这句话告诉 Claude。" with title "AI 接力" buttons {"好"} default button 1' >/dev/null 2>&1
    fi
  fi
  exit 1
fi
clear
echo "=== 接力总开关 ==="
/usr/bin/python3 "$ROOT/bin/relay" switch
echo
echo "（做完了，这个窗口可以关掉。）"
