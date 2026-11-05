#!/usr/bin/env python3
"""生成 Dictate 的单色 App 图标（assets/AppIcon.icns）。

只依赖 macOS 自带的 PyObjC / sips / iconutil：

    .venv/bin/python assets/make-icon.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSGraphicsContext,
    NSLineCapStyleRound,
    NSMakeRect,
    NSPNGFileType,
)

ROOT = Path(__file__).resolve().parent
ICONSET = ROOT / "AppIcon.iconset"
MASTER = ROOT / "AppIcon-1024.png"
SIZES = (16, 32, 128, 256, 512)


def draw() -> None:
    size = 1024
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0
    )

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(
        NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    )

    # 圆角底板：macOS 图标网格留出约 10% 边距。
    inset = 100
    board = NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
    NSColor.colorWithCalibratedWhite_alpha_(0.04, 1.0).set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(board, 186, 186).fill()

    NSColor.whiteColor().set()

    # 麦克风胶囊
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(422, 400, 180, 320), 90, 90
    ).fill()

    # 下方的拾音弧
    arc = NSBezierPath.bezierPath()
    arc.setLineWidth_(44)
    arc.setLineCapStyle_(NSLineCapStyleRound)
    arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        (512, 580), 190, 180, 360, False
    )
    arc.stroke()

    # 支架与底座
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(490, 320, 44, 84), 22, 22
    ).fill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(412, 270, 200, 44), 22, 22
    ).fill()

    NSGraphicsContext.restoreGraphicsState()

    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    if not data.writeToFile_atomically_(str(MASTER), True):
        raise SystemExit(f"无法写入 {MASTER}")


def build_icns() -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir()
    for base in SIZES:
        for scale in (1, 2):
            pixels = base * scale
            if pixels > 1024:
                continue
            suffix = "@2x" if scale == 2 else ""
            name = f"icon_{base}x{base}{suffix}.png"
            subprocess.run(
                ["sips", "-z", str(pixels), str(pixels), str(MASTER), "--out", str(ICONSET / name)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ROOT / "AppIcon.icns")],
        check=True,
    )
    shutil.rmtree(ICONSET)


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("这个脚本只能在 macOS 上运行")
    draw()
    build_icns()
    print("wrote", ROOT / "AppIcon.icns")
