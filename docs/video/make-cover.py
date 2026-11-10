#!/usr/bin/env python3
"""生成 B 站封面：深色底 + 大字「按住说话 / 松手上屏」。

只依赖 macOS 自带的 PyObjC：

    .venv/bin/python docs/video/make-cover.py

输出 docs/video/cover-1920x1080.png。
"""
from __future__ import annotations

import sys
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSKernAttributeName,
    NSMakeRect,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSPNGFileType,
    NSString,
)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cover-1920x1080.png"
W, H = 1920, 1080
ACCENT = (0.37, 0.92, 0.83, 1.0)  # 青绿


def color(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def font(size: float, bold: bool = True):
    names = ("PingFangSC-Semibold", "PingFang SC") if bold else ("PingFangSC-Regular",)
    for name in names:
        f = NSFont.fontWithName_size_(name, size)
        if f is not None:
            return f
    return NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)


def draw_text(text, rect, size, fill, bold=True, tracking=0.0, line_height=None):
    style = NSMutableParagraphStyle.alloc().init()
    style.setLineSpacing_((line_height or size * 1.18) - size)
    attrs = {
        NSFontAttributeName: font(size, bold),
        NSForegroundColorAttributeName: fill,
        NSParagraphStyleAttributeName: style,
        NSKernAttributeName: tracking,
    }
    NSString.alloc().initWithString_(text).drawInRect_withAttributes_(rect, attrs)


def draw_waveform(x, y, unit=14, gap=12, color_=None):
    heights = (1.4, 2.4, 3.6, 2.0, 3.0, 1.6, 2.6)
    color_ = color_ or color(*ACCENT)
    color_.set()
    for i, h in enumerate(heights):
        bar_h = unit * h
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x + i * (unit + gap), y, unit, bar_h), 4, 4
        ).fill()


def draw() -> None:
    rep = (
        NSBitmapImageRep.alloc()
        .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, W, H, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0
        )
    )
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(
        NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    )

    # 背景：近黑，右侧略亮，避免死黑
    color(0.035, 0.035, 0.045).set()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, W, H))
    color(0.06, 0.07, 0.08).set()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(1180, -260, 1100, 1100)).fill()

    # 顶部小字
    draw_text(
        "DICTATE  /  macOS",
        NSMakeRect(132, H - 156, 900, 40),
        26,
        color(0.62, 0.64, 0.68),
        bold=True,
        tracking=4.0,
    )

    # 标题
    draw_text(
        "按住说话",
        NSMakeRect(120, 470, 1500, 260),
        208,
        color(0.98, 0.98, 1.0),
        bold=True,
        tracking=-6.0,
    )
    draw_text(
        "松手上屏",
        NSMakeRect(120, 238, 1500, 260),
        208,
        color(0.98, 0.98, 1.0),
        bold=True,
        tracking=-6.0,
    )

    # 强调竖条
    color(*ACCENT).set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(88, 268, 12, 258), 6, 6
    ).fill()

    # 底部说明
    draw_text(
        "完全离线  ·  开源  ·  中英混说不切输入法",
        NSMakeRect(132, 150, 1200, 50),
        34,
        color(0.72, 0.75, 0.79),
        bold=False,
        tracking=1.0,
    )

    # 右下角波形
    draw_waveform(1516, 184, unit=20, gap=13)
    draw_text(
        "本地识别",
        NSMakeRect(1508, 136, 260, 40),
        24,
        color(0.46, 0.50, 0.55),
        bold=True,
        tracking=2.0,
    )

    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    if not data.writeToFile_atomically_(str(OUT), True):
        raise SystemExit(f"无法写入 {OUT}")


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("这个脚本只能在 macOS 上运行")
    draw()
    print("wrote", OUT)
