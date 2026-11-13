#!/bin/sh
# 交回 Claude（双击运行；可以拷到桌面用，那样要把下面的 ROOT 改成接力文件夹的完整路径）。
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -f "$ROOT/bin/relay" ]; then
  /usr/bin/osascript -e 'display dialog "没找到接力程序所在的盘，接力程序在那块盘上。\n\n请先插上那块盘，再双击一次。这次什么都没做。" with title "AI 接力" buttons {"好"} default button 1' >/dev/null 2>&1
  exit 1
fi
clear
echo "=== 交回 Claude ==="
/usr/bin/python3 "$ROOT/bin/relay" takeback
echo
echo "（做完了，这个窗口可以关掉。）"
