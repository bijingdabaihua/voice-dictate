#!/usr/bin/env bash
# 构建 Dictate.app（本地 ad-hoc 签名）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/Dictate.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$ROOT/packaging/Info.plist" "$APP/Contents/Info.plist"
clang -O2 -DROOT="\"$ROOT\"" \
  -o "$APP/Contents/MacOS/Dictate" \
  "$ROOT/packaging/launcher.c"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

echo "built: $APP"
