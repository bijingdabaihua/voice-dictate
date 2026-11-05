#!/usr/bin/env bash
# 开发用：构建一个依赖仓库 .venv 的 Dictate.app。
# 发布版请用 build-app.sh（自包含 Python + 依赖）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/Dictate.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$ROOT/packaging/Info.plist" "$APP/Contents/Info.plist"
clang -O2 -o "$APP/Contents/MacOS/Dictate" "$ROOT/packaging/launcher.c"

if [ -f "$ROOT/assets/AppIcon.icns" ]; then
  mkdir -p "$APP/Contents/Resources"
  cp "$ROOT/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi

xattr -cr "$APP" >/dev/null 2>&1 || true
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "built dev app: $APP"
