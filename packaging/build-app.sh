#!/usr/bin/env bash
# 构建自包含的 Dictate.app / Dictate-<version>.dmg。
#
# 依赖：
#   - Xcode Command Line Tools（clang）
#   - 网络（首次下载可移植 Python 和 wheel）
#
# 可选环境变量：
#   PY_VERSION=3.12.16      可移植 Python 版本
#   PBS_TAG=20261026        python-build-standalone release tag
#   PIP_INDEX_URL=...       pip 镜像（国内建议清华源）
#   CODESIGN_IDENTITY=...   签名身份；不设置则 ad-hoc 签名
#   SKIP_DMG=1              只构建 .app
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ROOT/packaging/Info.plist")"
PY_VERSION="${PY_VERSION:-3.12.16}"
PBS_TAG="${PBS_TAG:-20261026}"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
APP="$DIST/Dictate.app"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PY_VERSION}%2B${PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz"

mkdir -p "$BUILD" "$DIST"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$APP/Contents/Resources/site-packages"

# 1. 可移植 Python（缓存到 build/）
if [ ! -x "$BUILD/python/bin/python3" ]; then
  echo "==> 下载可移植 Python ${PY_VERSION}（${PBS_TAG}）"
  if [ ! -f "$BUILD/python.tar.gz" ] || ! tar -tzf "$BUILD/python.tar.gz" >/dev/null 2>&1; then
    curl -L --fail --retry 5 --retry-delay 3 -C - -o "$BUILD/python.tar.gz" "$PY_URL"
  fi
  rm -rf "$BUILD/python"
  tar -xzf "$BUILD/python.tar.gz" -C "$BUILD"
  test -x "$BUILD/python/bin/python3"
fi
cp -R "$BUILD/python" "$APP/Contents/Resources/python"
# 解释器放到 Contents/MacOS，NSBundle 才会把进程识别成 Dictate.app，
# 权限弹窗和系统设置里显示的是 Dictate，而不是 python3。
cp "$APP/Contents/Resources/python/bin/python3" "$APP/Contents/MacOS/DictatePython"

# 2. Python 依赖（直接装进 App，不依赖用户环境；按 requirements 哈希缓存）
REQ_HASH="$(shasum -a 256 "$ROOT/requirements-app.txt" | awk '{print $1}')"
DEPS_MARKER="$BUILD/.deps-$REQ_HASH"
if [ ! -f "$DEPS_MARKER" ]; then
  echo "==> 安装依赖"
  rm -rf "$BUILD/site-packages"
  mkdir -p "$BUILD/site-packages"
  "$APP/Contents/Resources/python/bin/python3" -m ensurepip --upgrade >/dev/null 2>&1 || true
  PIP_ARGS=(install --no-cache-dir --target "$BUILD/site-packages" -r "$ROOT/requirements-app.txt")
  if [ -n "${PIP_INDEX_URL:-}" ]; then
    PIP_ARGS+=(--index-url "$PIP_INDEX_URL")
  fi
  "$APP/Contents/Resources/python/bin/python3" -m pip "${PIP_ARGS[@]}"
  touch "$DEPS_MARKER"
fi
rm -rf "$APP/Contents/Resources/site-packages"
cp -R "$BUILD/site-packages" "$APP/Contents/Resources/site-packages"

# 3. 应用代码
cp "$ROOT/dictate.py" "$APP/Contents/Resources/app/dictate.py"
cp "$ROOT/packaging/Info.plist" "$APP/Contents/Info.plist"
if [ -f "$ROOT/assets/AppIcon.icns" ]; then
  mkdir -p "$APP/Contents/Resources"
  cp "$ROOT/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi

# 4. 启动壳
clang -O2 -o "$APP/Contents/MacOS/Dictate" "$ROOT/packaging/launcher.c"

# 5. 清掉构建过程产生的缓存，避免把用户目录信息打进包
find "$APP/Contents/Resources/site-packages" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP/Contents/Resources/site-packages" -name '*.pyc' -delete 2>/dev/null || true
find "$APP/Contents/Resources/python" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP/Contents/Resources/python" -name '*.pyc' -delete 2>/dev/null || true
xattr -cr "$APP" >/dev/null 2>&1 || true

# 6. 签名（有 Developer ID 就用，否则 ad-hoc）
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  echo "==> 签名：$CODESIGN_IDENTITY"
  codesign --force --deep --options runtime --sign "$CODESIGN_IDENTITY" "$APP"
else
  echo "==> ad-hoc 签名（未公证；用户首次打开需右键 → 打开）"
  codesign --force --deep --sign - "$APP"
fi

echo "built: $APP"
du -sh "$APP"

# 7. DMG（带 Applications 快捷方式，拖进去即可安装）
if [ "${SKIP_DMG:-0}" != "1" ]; then
  DMG="$DIST/Dictate-${VERSION}.dmg"
  STAGE="$BUILD/dmg"
  rm -rf "$STAGE"
  mkdir -p "$STAGE"
  cp -Rc "$APP" "$STAGE/Dictate.app"
  ln -s /Applications "$STAGE/Applications"
  rm -f "$DMG"
  hdiutil create -volname "Dictate" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
  rm -rf "$STAGE"
  echo "built: $DMG"
fi
