#!/bin/sh
# ai-relay installer / 安装脚本 (macOS).
# What it does / 它做什么:
#   1. config/projects.json        <- copied from the example if missing / 没有就从样例拷一份
#   2. ~/.claude/relay-shim.sh     <- hook shim with this folder's path filled in / 钩子垫片
#   3. ~/.claude/skills/relay-*    <- two Claude Code skills / 两个 Claude 技能
#   4. ~/Applications/Relay.app    <- menu-bar app / 菜单栏图标
# It never edits any settings.json. Existing files are backed up first (*.bak-<time>), never deleted.
# 它不改任何 settings.json；已有的文件先备份（*.bak-时间），不删除。
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"
case "$ROOT" in *"|"*|*"&"*|*'"'*) echo "Folder path contains | & or \" — please move it. / 路径里有特殊字符，请换个位置。"; exit 1;; esac
backup() { [ -e "$1" ] && cp -Rp "$1" "$1.bak-$TS" && echo "  backed up / 已备份: $1.bak-$TS"; return 0; }
render() { /usr/bin/sed -e "s|__RELAY_ROOT__|$ROOT|g" -e "s|__HOME__|$HOME|g" "$1" > "$2"; }

echo "1/4 config"
if [ ! -f "$ROOT/config/projects.json" ]; then
  cp "$ROOT/config/projects.example.json" "$ROOT/config/projects.json"
  echo "  created config/projects.json — edit it before first use / 已生成，请先改好再用"
else
  echo "  config/projects.json exists, left as is / 已存在，没动"
fi

echo "2/4 hook shim"
mkdir -p "$HOME/.claude"
backup "$HOME/.claude/relay-shim.sh"
render "$ROOT/shim/relay-shim.sh" "$HOME/.claude/relay-shim.sh"
chmod 700 "$HOME/.claude/relay-shim.sh"

echo "3/4 Claude Code skills"
for s in relay-handoff relay-takeback; do
  mkdir -p "$HOME/.claude/skills/$s"
  backup "$HOME/.claude/skills/$s/SKILL.md"
  render "$ROOT/skills/$s/SKILL.md" "$HOME/.claude/skills/$s/SKILL.md"
done

echo "4/4 menu-bar app"
if [ -x /usr/bin/swiftc ]; then
  sh "$ROOT/menubar/build.sh" "$HOME/Applications"
  echo "  start it / 启动: open \"$HOME/Applications/Relay.app\""
else
  echo "  swiftc not found (install Xcode Command Line Tools); skipped. The CLI still works."
  echo "  没找到 swiftc（需要装 Xcode 命令行工具），跳过；命令行照样能用。"
fi

render "$ROOT/config/claude-hooks.example.json" "$ROOT/config/claude-hooks.json"
cat <<MSG

Done. Two things are left to you / 还有两件事要你自己做:
  a) Edit / 修改  $ROOT/config/projects.json  (projects, model names, permissions)
  b) For each project Claude Code should be guarded in, merge the "hooks" block of
     $ROOT/config/claude-hooks.json
     into that project's .claude/settings.json.
     想让 Claude 在交出期间被拦住的工程：把上面这个文件里的 hooks 合并进该工程的 .claude/settings.json。
Check / 检查:  /usr/bin/python3 "$ROOT/bin/relay" status
MSG
