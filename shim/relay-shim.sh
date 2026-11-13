#!/bin/sh
# 接力垫片（放在内置盘 ~/.claude/relay-shim.sh；正本在接力文件夹的 shim/，由 install.sh 装过去）。
# 钩子不直接调接力文件夹里的脚本（它可能在外接盘上），而是调这个垫片：
#   盘在   → 转交给盘上的脚本；
#   盘不在 → 记录类钩子直接放行；拦截钩子看内置盘上的“持棒者抄本”。
# 用法：relay-shim.sh guard | append | stop | stopfail | probe
# 退出码：0＝放行；2＝拦下（理由写到 stderr，Claude 会看到）。

MODE="$1"
ROOT="${RELAY_ROOT:-__RELAY_ROOT__}"
HOLDERS="${RELAY_HOLDERS:-$HOME/.claude/relay-holders.json}"
PY=/usr/bin/python3

# ---- 记录类：无论如何都放行 ----
if [ "$MODE" != "guard" ]; then
  case "$MODE" in
    append)   [ -f "$ROOT/bin/hook_baton.py" ] && "$PY" "$ROOT/bin/hook_baton.py" append ;;
    stop)     [ -f "$ROOT/bin/hook_baton.py" ] && "$PY" "$ROOT/bin/hook_baton.py" rewrite ;;
    stopfail) [ -f "$ROOT/bin/hook_baton.py" ] && "$PY" "$ROOT/bin/hook_baton.py" rewrite --failed ;;
    probe)    [ -f "$ROOT/bin/hook_probe.py" ] && "$PY" "$ROOT/bin/hook_probe.py" ;;
  esac
  exit 0
fi

# ---- 拦截类：出任何岔子都拦下（退出码 2） ----
DONE=0
bail() {
  if [ "$DONE" != "1" ]; then
    echo "接力系统的拦截检查没能正常完成，为安全起见先拦下这次操作。请点菜单栏“接力”里的“接力总开关”查看。" >&2
    exit 2
  fi
}
trap bail EXIT
trap 'exit 2' HUP INT TERM

if [ -f "$ROOT/bin/hook_guard.py" ]; then
  "$PY" "$ROOT/bin/hook_guard.py"
  RC=$?
  if [ "$RC" = "0" ]; then DONE=1; exit 0; fi
  if [ "$RC" = "2" ]; then DONE=1; exit 2; fi
  exit 2    # 其它退出码：走 bail，拦下
fi

# 外接盘不在：看内置盘抄本
"$PY" -c '
import json, os, sys
def out(code, msg=""):
    if msg: sys.stderr.write(msg + "\n")
    os._exit(code)
try:
    data = json.loads(sys.stdin.read() or "{}")
    with open(sys.argv[1], encoding="utf-8") as f:
        copy = json.load(f)
    projects = copy["projects"]
    if copy.get("live_enabled") is False:
        out(0)
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    cands = [cwd, os.environ.get("CLAUDE_PROJECT_DIR"), ti.get("file_path"), ti.get("notebook_path")]
    for p in cands:
        if not p or not isinstance(p, str):
            continue
        rp = os.path.realpath(p if os.path.isabs(p) else os.path.join(cwd, p))
        for code, info in projects.items():
            root = info["path"].rstrip("/")
            if rp == root or rp.startswith(root + "/"):
                if info["holder"] != "claude":
                    out(2, "接力程序所在的盘没插，接力状态不明（抄本显示这个工程不是 Claude 持棒）。请先插上那块盘；急用可点菜单栏“接力”里的“接力总开关”。")
    out(0)
except BaseException:
    out(2, "接力程序所在的盘没插，而且内置盘上的持棒者抄本读不出来，为安全起见先拦下。请插上那块盘，或点菜单栏“接力”里的“接力总开关”。")
' "$HOLDERS"
RC=$?
if [ "$RC" = "0" ]; then DONE=1; exit 0; fi
if [ "$RC" = "2" ]; then DONE=1; exit 2; fi
exit 2
