#!/bin/sh
# Build the desktop + menu-bar app (Relay.app / AI 接力台) with the system Swift compiler.
# 把 main.swift 编成独立桌面软件 Relay.app（含主窗口、Dock 图标、菜单栏快捷图标与微信打赏收款码资源）。
# Usage / 用法: menubar/build.sh [output dir]   default / 默认: menubar/build/
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$HERE/build}"
APP="$OUT/Relay.app"
mkdir -p "$OUT"
rm -rf "$APP.new"
mkdir -p "$APP.new/Contents/MacOS" "$APP.new/Contents/Resources"
/usr/bin/swiftc -O "$HERE/main.swift" -o "$APP.new/Contents/MacOS/RelayMenu"
# Emergency switch for when the relay folder's drive is unplugged: bundle a copy with the path baked in.
/usr/bin/sed "s|^ROOT=.*|ROOT=\"$ROOT\"|" "$ROOT/launchers/接力总开关.command" > "$APP.new/Contents/Resources/switch-offline.command"
if [ -f "$HERE/donate_qr.png" ]; then
  cp "$HERE/donate_qr.png" "$APP.new/Contents/Resources/donate_qr.png"
fi
cat > "$APP.new/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>local.ai-relay.studio</string>
  <key>CFBundleName</key><string>AI 接力台</string>
  <key>CFBundleDisplayName</key><string>AI 接力台</string>
  <key>CFBundleExecutable</key><string>RelayMenu</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><false/>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>RelayRoot</key><string>$ROOT</string>
</dict>
</plist>
PLIST
/usr/bin/codesign --force -s - "$APP.new" >/dev/null 2>&1 || true
if [ -d "$APP" ]; then mv "$APP" "$APP.old.$$"; fi
mv "$APP.new" "$APP"
echo "Built / 编好了: $APP"
[ -d "$APP.old.$$" ] && echo "Previous build kept at / 旧版本留在: $APP.old.$$"
exit 0
