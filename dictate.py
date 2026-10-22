#!/usr/bin/env python3
"""Qwen3-ASR 全局听写（菜单栏 App）

按住热键说话，松开后自动识别并粘贴到当前光标处。
默认热键：左 Option（Windows 键盘上的左 Alt）。

首次运行需要授权：
  1. 麦克风
  2. 辅助功能（模拟 Cmd+V）
  3. 输入监控（监听全局热键）
授权后如果热键没反应，退出重开一次即可。
"""

from __future__ import annotations

import atexit
import ctypes
import json
import logging
import queue
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import rumps

os_import_error = None
try:
    import Quartz
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSColor,
        NSFont,
        NSFontAttributeName,
        NSFloatingWindowLevel,
        NSImage,
        NSLineBreakByWordWrapping,
        NSMakeRect,
        NSOperationQueue,
        NSPanel,
        NSPasteboard,
        NSPasteboardItem,
        NSPasteboardTypeString,
        NSScreen,
        NSSound,
        NSTextAlignmentLeft,
        NSTextField,
        NSView,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorIgnoresCycle,
        NSWindowCollectionBehaviorTransient,
        NSWindowStyleMaskBorderless,
        NSWindowStyleMaskNonactivatingPanel,
        NSString,
        NSStringDrawingUsesLineFragmentOrigin,
    )
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )
except Exception as exc:  # noqa: BLE001
    os_import_error = exc

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "dictation.json"
HOTWORDS_FILE = ROOT / "hotwords.txt"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "dictate.log"
SR = 16000

DEFAULT_CONFIG = {
    "hotkey_keycode": 58,  # 左 Option
    "hotkey_name": "左 Option",
    "auto_enter": False,
    "restore_clipboard": True,
    "mute_audio": True,
    "model": "models/Qwen3-ASR-0.6B",
    "device": None,
    "max_record_seconds": 60,
}

# 修饰键 keycode -> (名称, 设备相关 flag)
MODIFIER_KEYS = {
    54: ("右 Command", 0x00000010),
    55: ("左 Command", 0x00000008),
    56: ("左 Shift", 0x00000002),
    60: ("右 Shift", 0x00000004),
    58: ("左 Option", 0x00000020),
    61: ("右 Option", 0x00000040),
    59: ("左 Control", 0x00000001),
    62: ("右 Control", 0x00002000),
    63: ("Fn", 0x00800000),
}

# 设备相关位不存在时的兜底：同一修饰键的通用 flag
GENERIC_MODIFIER_MASKS = {
    54: 0x00100000, 55: 0x00100000,  # Command
    56: 0x00020000, 60: 0x00020000,  # Shift
    58: 0x00080000, 61: 0x00080000,  # Option
    59: 0x00040000, 62: 0x00040000,  # Control
    63: 0x00800000,                   # Fn
}

KEY_NAMES = {
    36: "Return",
    48: "Tab",
    49: "Space",
    53: "Esc",
    96: "F5",
    97: "F6",
    98: "F7",
    99: "F3",
    100: "F8",
    101: "F9",
    103: "F11",
    105: "F13",
    107: "F14",
    113: "F15",
}

KEY_V = 9
KEY_RETURN = 36
KEY_ESC = 53


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(
                LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            logging.warning("读取配置失败，使用默认值：%s", exc)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_context(extra: str = "") -> str:
    """热词来自 hotwords.txt，按空格拼接（Qwen3-ASR 的 context 格式）。"""
    terms: list[str] = []
    if HOTWORDS_FILE.exists():
        for line in HOTWORDS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    if extra:
        terms.extend(extra.split())
    return " ".join(dict.fromkeys(terms))


def strip_context_echo(text: str, context: str) -> str:
    """流式解码的第一块偶尔会把 context 原样吐出来，这里去掉。"""
    text = (text or "").strip()
    context = (context or "").strip()
    if not text or not context:
        return text
    if text.startswith(context):
        return text[len(context) :].lstrip(" ，。,.、")
    if len(text) < len(context) and context.startswith(text):
        return ""
    return text


def to_simplified(text: str) -> str:
    """模型偶尔会输出繁体（流式解码更常见），统一转成简体。"""
    if not text:
        return text
    try:
        return NSString.stringWithString_(text).stringByApplyingTransform_reverse_(
            "Traditional-Simplified", False
        )
    except Exception:  # noqa: BLE001
        return text


def key_name(keycode: int) -> str:
    if keycode in MODIFIER_KEYS:
        return MODIFIER_KEYS[keycode][0]
    return KEY_NAMES.get(keycode, f"keycode {keycode}")


def input_monitoring_status() -> str:
    """查询「输入监控」权限（不弹窗）。0=已授权，1=被拒绝，2=未决定。"""
    try:
        import ctypes

        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
        iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
        access = iokit.IOHIDCheckAccess(1)  # kIOHIDRequestTypeListenEvent
        return {0: "已授权", 1: "被拒绝", 2: "未决定"}.get(access, f"未知({access})")
    except Exception as exc:  # noqa: BLE001
        return f"检查失败：{exc}"


def request_input_monitoring() -> None:
    """触发系统的「输入监控」授权弹窗（该权限不会自动弹）。"""
    try:
        import ctypes

        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]
        iokit.IOHIDRequestAccess.restype = ctypes.c_bool
        iokit.IOHIDRequestAccess(1)  # kIOHIDRequestTypeListenEvent
    except Exception:  # noqa: BLE001
        logging.exception("请求输入监控权限失败")


def ui_thread(fn) -> None:
    """把 UI 更新丢回主线程。

    Python 异常若从 block 里逃出去，PyObjC 会把它转成 ObjC 异常导致整个
    App abort（崩溃报告里就是这种）。这里统一兜住并记日志。
    """

    def wrapped():
        try:
            fn()
        except Exception:  # noqa: BLE001
            logging.exception("UI 回调执行失败")

    NSOperationQueue.mainQueue().addOperationWithBlock_(wrapped)


def play_sound(name: str) -> None:
    try:
        NSSound.soundNamed_(name).play()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 系统输出静音
# ---------------------------------------------------------------------------

class AudioSilencer:
    """录音时静音默认输出设备，结束后恢复成原来的状态。

    用 CoreAudio 公共 API，不依赖具体播放器：汽水、网易云、Chrome、视频
    都能一并静音。音乐只是听不见，并不会暂停。
    """

    _SYS = 1
    _GLOBAL = 0x676C6F62       # 'glob'
    _OUTPUT = 0x6F757470       # 'outp'
    _DEFAULT_OUT = 0x644F7574  # 'dOut'
    _MUTE = 0x6D757465         # 'mute'

    class _Addr(ctypes.Structure):
        _fields_ = [
            ("selector", ctypes.c_uint32),
            ("scope", ctypes.c_uint32),
            ("element", ctypes.c_uint32),
        ]

    def __init__(self):
        self._lock = threading.Lock()
        self._device = None
        self._was_muted = False
        self._ca = None
        try:
            ca = ctypes.CDLL(
                "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
            )
            ca.AudioObjectGetPropertyData.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(self._Addr),
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.c_void_p,
            ]
            ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
            ca.AudioObjectSetPropertyData.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(self._Addr),
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
            self._ca = ca
        except Exception:  # noqa: BLE001
            logging.exception("加载 CoreAudio 失败，静音功能不可用")

    def _addr(self, selector: int, scope: int) -> "AudioSilencer._Addr":
        return self._Addr(selector, scope, 0)

    def _default_device(self) -> int | None:
        value = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(value))
        addr = self._addr(self._DEFAULT_OUT, self._GLOBAL)
        status = self._ca.AudioObjectGetPropertyData(
            self._SYS, ctypes.byref(addr), 0, None, ctypes.byref(size),
            ctypes.byref(value),
        )
        return value.value if status == 0 else None

    def _get_mute(self, device: int) -> int | None:
        value = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(value))
        addr = self._addr(self._MUTE, self._OUTPUT)
        status = self._ca.AudioObjectGetPropertyData(
            device, ctypes.byref(addr), 0, None, ctypes.byref(size),
            ctypes.byref(value),
        )
        return value.value if status == 0 else None

    def _set_mute(self, device: int, muted: bool) -> bool:
        value = ctypes.c_uint32(1 if muted else 0)
        addr = self._addr(self._MUTE, self._OUTPUT)
        status = self._ca.AudioObjectSetPropertyData(
            device, ctypes.byref(addr), 0, None, ctypes.sizeof(value),
            ctypes.byref(value),
        )
        return status == 0

    def mute(self) -> bool:
        """静音默认输出设备；已静音则什么都不做。"""
        if self._ca is None:
            return False
        with self._lock:
            if self._device is not None:
                return False
            device = self._default_device()
            if device is None:
                logging.warning("取不到默认输出设备，无法静音")
                return False
            previous = self._get_mute(device)
            if previous is None or not self._set_mute(device, True):
                logging.warning("默认输出设备不支持静音")
                return False
            self._device = device
            self._was_muted = bool(previous)
            return True

    def restore(self) -> bool:
        """把设备恢复到录音前的静音状态。"""
        if self._ca is None:
            return False
        with self._lock:
            if self._device is None:
                return False
            self._set_mute(self._device, self._was_muted)
            self._device = None
            return True


# ---------------------------------------------------------------------------
# 悬浮字幕窗
# ---------------------------------------------------------------------------

class HUDWindow(NSPanel):
    """不抢焦点的浮窗。"""

    def canBecomeKeyWindow(self):  # noqa: N802
        return False

    def canBecomeMainWindow(self):  # noqa: N802
        return False


class HUD:
    WIDTH = 700
    FONT_SIZE = 19
    LINE_HEIGHT = 23
    MAX_LINES = 6
    MAX_CHARS = 200
    BASE_HEIGHT = 79  # 一行文字时的窗口高度

    def __init__(self):
        self.panel = None
        self.label = None
        self.status_label = None

    def _build(self) -> None:
        width, height = self.WIDTH, self.BASE_HEIGHT
        screen = NSScreen.mainScreen().visibleFrame()
        x = screen.origin.x + (screen.size.width - width) / 2
        y = screen.origin.y + 140

        panel = HUDWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, width, height),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setHasShadow_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorTransient
            | NSWindowCollectionBehaviorIgnoresCycle
        )

        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.84).CGColor()
        )
        layer.setCornerRadius_(16.0)
        panel.setContentView_(content)

        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(24, height - 32, width - 48, 18)
        )
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_(12))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.62))
        status.setStringValue_("")
        content.addSubview_(status)

        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(24, 16, width - 48, height - 56)
        )
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(NSFont.systemFontOfSize_(self.FONT_SIZE))
        label.setTextColor_(NSColor.whiteColor())
        label.setAlignment_(NSTextAlignmentLeft)
        label.setLineBreakMode_(NSLineBreakByWordWrapping)
        label.setUsesSingleLineMode_(False)
        label.cell().setWraps_(True)
        label.setMaximumNumberOfLines_(self.MAX_LINES)
        label.setStringValue_("")
        content.addSubview_(label)

        self.panel, self.label, self.status_label = panel, label, status

    @staticmethod
    def _clip(text: str) -> str:
        text = (text or "").strip()
        if len(text) > HUD.MAX_CHARS:
            return "…" + text[-HUD.MAX_CHARS :]
        return text

    def _text_height(self, text: str) -> int:
        if not text:
            return self.LINE_HEIGHT
        s = NSString.stringWithString_(text)
        attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(self.FONT_SIZE)}
        rect = s.boundingRectWithSize_options_attributes_(
            (self.WIDTH - 48, 10000),
            NSStringDrawingUsesLineFragmentOrigin,
            attrs,
        )
        measured = max(int(round(rect.size.height)), self.LINE_HEIGHT)
        return min(measured, self.LINE_HEIGHT * self.MAX_LINES)

    def _layout(self, text: str) -> None:
        text = self._clip(text)
        self.label.setStringValue_(text)
        height = int(self._text_height(text)) + 56
        if int(self.panel.frame().size.height) == height:
            return
        frame = self.panel.frame()
        # 保持底边不变，让窗口向上变高
        self.panel.setFrame_display_(
            NSMakeRect(frame.origin.x, frame.origin.y, self.WIDTH, height), True
        )
        self.panel.contentView().setFrame_(NSMakeRect(0, 0, self.WIDTH, height))
        self.status_label.setFrame_(
            NSMakeRect(24, height - 32, self.WIDTH - 48, 18)
        )
        self.label.setFrame_(NSMakeRect(24, 16, self.WIDTH - 48, height - 56))

    def show(self, status: str = "● 正在听…", text: str = "") -> None:
        if self.panel is None:
            self._build()
        self.status_label.setStringValue_(status)
        self._layout(text)
        self.panel.orderFrontRegardless()

    def set_status(self, status: str) -> None:
        if self.panel is None:
            self.show(status)
        else:
            self.status_label.setStringValue_(status)

    def set_text(self, text: str) -> None:
        if self.panel is None:
            self.show("● 正在听…", text)
        else:
            self._layout(text)

    def hide(self) -> None:
        if self.panel is not None:
            self.panel.orderOut_(None)


# ---------------------------------------------------------------------------
# 录音
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self, device=None):
        self.device = device
        self.stream = None
        self.chunks: list[np.ndarray] = []
        self.lock = threading.Lock()
        self.started_at = 0.0
        self.on_block = None  # 每收到一块音频时调用（在音频线程里）

    def start(self) -> None:
        import sounddevice as sd

        with self.lock:
            self.chunks = []
        self.started_at = time.perf_counter()

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                logging.warning("音频状态：%s", status)
            block = indata[:, 0].copy()
            with self.lock:
                self.chunks.append(block)
            if self.on_block is not None:
                try:
                    self.on_block(block)
                except Exception:  # noqa: BLE001
                    logging.exception("处理音频块失败")

        self.stream = sd.InputStream(
            samplerate=SR,
            channels=1,
            dtype="float32",
            blocksize=int(0.05 * SR),
            device=self.device,
            callback=callback,
        )
        self.stream.start()

    def stop(self) -> np.ndarray:
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001
                logging.warning("关闭音频流失败：%s", exc)
        with self.lock:
            chunks = list(self.chunks)
            self.chunks = []
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks)

    def cancel(self) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# 全局热键
# ---------------------------------------------------------------------------

class HotkeyTap:
    """用 CGEventTap 监听全局热键；同时负责按键学习。"""

    def __init__(self, on_press, on_release, on_cancel, on_bind):
        self.on_press = on_press
        self.on_release = on_release
        self.on_cancel = on_cancel
        self.on_bind = on_bind
        self.keycode = 58
        self.learning = False
        self.down = False
        self.tap = None
        self._thread = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        """系统在安全输入（密码框/授权弹窗）时会停用 tap，之后不会自动恢复。"""
        reported = False
        while True:
            time.sleep(2.0)
            tap = self.tap
            if tap is None:
                continue
            if Quartz.CGEventTapIsEnabled(tap):
                if reported:
                    logging.info("事件监听已恢复")
                    reported = False
                continue
            if not reported:
                logging.warning("事件监听处于停用状态，尝试重新启用")
                reported = True
            Quartz.CGEventTapEnable(tap, True)

    def begin_learning(self) -> None:
        self.down = False
        self.learning = True

    def _is_modifier(self, keycode: int) -> bool:
        return keycode in MODIFIER_KEYS

    def _pressed(self, keycode: int, flags: int) -> bool:
        mask = MODIFIER_KEYS.get(keycode, (None, 0))[1]
        if flags & mask:
            return True
        # 个别键盘/系统不会在 flags 里给设备相关位（低 16 位），
        # 此时退回通用修饰键位，否则修饰键永远判不出按下。
        generic = GENERIC_MODIFIER_MASKS.get(keycode)
        if generic and not (flags & 0xFFFF):
            return bool(flags & generic)
        return False

    def _handle(self, event_type, event) -> None:
        keycode = int(
            Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
        )

        if self.learning:
            if event_type == Quartz.kCGEventFlagsChanged:
                flags = int(Quartz.CGEventGetFlags(event))
                if self._is_modifier(keycode) and self._pressed(keycode, flags):
                    self.learning = False
                    self.on_bind(keycode)
            elif event_type == Quartz.kCGEventKeyDown:
                self.learning = False
                self.on_bind(keycode)
            return

        if self._is_modifier(self.keycode):
            if event_type != Quartz.kCGEventFlagsChanged or keycode != self.keycode:
                return
            flags = int(Quartz.CGEventGetFlags(event))
            pressed = self._pressed(keycode, flags)
            if pressed and not self.down:
                self.down = True
                self.on_press()
            elif not pressed and self.down:
                self.down = False
                self.on_release()
            return

        # 非修饰键：keyDown/keyUp 就是按住/松开
        if keycode != self.keycode:
            if event_type == Quartz.kCGEventKeyDown and keycode == KEY_ESC:
                self.on_cancel()
            return
        if event_type == Quartz.kCGEventKeyDown:
            if not self.down:
                self.down = True
                self.on_press()
        elif event_type == Quartz.kCGEventKeyUp:
            if self.down:
                self.down = False
                self.on_release()

    def _run(self) -> None:
        mask = (
            (1 << Quartz.kCGEventFlagsChanged)
            | (1 << Quartz.kCGEventKeyDown)
            | (1 << Quartz.kCGEventKeyUp)
        )
        while True:
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask,
                self._callback,
                None,
            )
            if tap is None:
                logging.error(
                    "创建全局热键监听失败：请在「系统设置 → 隐私与安全性」里给本程序"
                    "打开「辅助功能」和「输入监控」权限"
                )
                time.sleep(5)
                continue
            self.tap = tap
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            Quartz.CFRunLoopAddSource(
                Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
            )
            Quartz.CGEventTapEnable(tap, True)
            logging.info("全局热键监听已启动（keycode=%s）", self.keycode)
            Quartz.CFRunLoopRun()

    def _callback(self, proxy, event_type, event, refcon):  # noqa: ANN001
        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            logging.warning("事件监听被系统暂停（type=%s），重新启用", event_type)
            if self.tap is not None:
                Quartz.CGEventTapEnable(self.tap, True)
            return event
        try:
            self._handle(event_type, event)
        except Exception:  # noqa: BLE001
            logging.exception("处理键盘事件失败")
        return event


# ---------------------------------------------------------------------------
# 剪贴板 / 模拟按键
# ---------------------------------------------------------------------------

def snapshot_pasteboard(pb):
    saved = []
    items = pb.pasteboardItems()
    if not items:
        return saved
    for item in items:
        data = {}
        for t in item.types():
            d = item.dataForType_(t)
            if d is not None:
                data[str(t)] = d
        if data:
            saved.append(data)
    return saved


def restore_pasteboard(pb, saved) -> None:
    pb.clearContents()
    if not saved:
        return
    items = []
    for data in saved:
        item = NSPasteboardItem.alloc().init()
        ok = False
        for t, d in data.items():
            if item.setData_forType_(d, t):
                ok = True
        if ok:
            items.append(item)
    if items:
        pb.writeObjects_(items)


def post_key(keycode: int, command: bool = False) -> None:
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(source, keycode, down)
        if command:
            Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.01)


def paste_text(text: str, restore: bool, auto_enter: bool) -> bool:
    trusted = AXIsProcessTrusted()
    if not trusted:
        # 仍尝试发送 Cmd+V：某些情况下权限由父进程继承。
        # 但保留剪贴板内容，方便用户手动粘贴。
        logging.warning("辅助功能权限未授权，尝试发送 Cmd+V，并保留剪贴板内容")

    pb = NSPasteboard.generalPasteboard()
    saved = snapshot_pasteboard(pb) if (restore and trusted) else []

    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    time.sleep(0.02)
    post_key(KEY_V, command=True)
    if auto_enter:
        time.sleep(0.12)
        post_key(KEY_RETURN)
    if restore and trusted:
        time.sleep(0.45)
        restore_pasteboard(pb, saved)
    return trusted


# ---------------------------------------------------------------------------
# 菜单栏 App
# ---------------------------------------------------------------------------

class DictateApp(rumps.App):
    def __init__(self, cfg: dict):
        super().__init__("Dictate", title="⏬", quit_button=None)
        self.cfg = cfg
        self.session = None
        self.context = ""
        self.state = "loading"  # loading / idle / recording / processing / error
        self.recorder = Recorder(device=cfg.get("device"))
        self.recorder.on_block = self._on_audio_block
        self.transcribe_lock = threading.Lock()
        self.record_timer = None
        self.silencer = AudioSilencer()
        self.mute_timer = None
        self.silence_gen = 0
        atexit.register(self.silencer.restore)

        self.hud = HUD()
        self.hud_gen = 0
        self.hud_hide_timer = None
        self.stream_queue = None
        self.stream_stop = None
        self.stream_thread = None
        self.stream_failed = False
        self._last_level = 0.0
        self.model_path_str = ""

        self.status_item = rumps.MenuItem("模型加载中…")
        self.hotkey_item = rumps.MenuItem(
            f"热键：{cfg.get('hotkey_name', key_name(cfg['hotkey_keycode']))}"
        )
        self.auto_enter_item = rumps.MenuItem(
            "说完自动回车", callback=self.toggle_auto_enter
        )
        self.auto_enter_item.state = 1 if cfg.get("auto_enter") else 0
        self.restore_item = rumps.MenuItem(
            "粘贴后恢复剪贴板", callback=self.toggle_restore
        )
        self.restore_item.state = 1 if cfg.get("restore_clipboard") else 0
        self.mute_item = rumps.MenuItem(
            "听写时静音其他声音", callback=self.toggle_mute
        )
        self.mute_item.state = 1 if cfg.get("mute_audio", True) else 0

        self.menu = [
            self.status_item,
            None,
            self.hotkey_item,
            rumps.MenuItem("重新绑定热键…", callback=self.begin_rebind),
            None,
            self.auto_enter_item,
            self.restore_item,
            self.mute_item,
            None,
            rumps.MenuItem("检查权限…", callback=self.check_permissions),
            rumps.MenuItem("测试悬浮窗", callback=self.test_hud),
            rumps.MenuItem("打开日志", callback=self.open_log),
            rumps.MenuItem("退出", callback=self.quit),
        ]

        self.tap = HotkeyTap(
            on_press=self.on_hotkey_press,
            on_release=self.on_hotkey_release,
            on_cancel=self.on_cancel,
            on_bind=self.on_bind,
        )
        self.tap.keycode = int(cfg["hotkey_keycode"])

    # -- 启动 ---------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self.load_model, daemon=True).start()
        self.tap.start()
        trusted = AXIsProcessTrusted()
        monitoring = input_monitoring_status()
        logging.info(
            "权限状态：辅助功能=%s，输入监控=%s",
            "已授权" if trusted else "未授权",
            monitoring,
        )
        if monitoring != "已授权":
            request_input_monitoring()
        if not trusted:
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            logging.warning("尚未获得辅助功能权限，已弹出系统授权提示")

    def load_model(self) -> None:
        from mlx_qwen3_asr import Session

        model = self.cfg.get("model", DEFAULT_CONFIG["model"])
        model_path = Path(model)
        if not model_path.is_absolute():
            model_path = ROOT / model_path
        started = time.perf_counter()
        logging.info("加载模型：%s", model_path)
        try:
            self.model_path_str = str(model_path)
            self.session = Session(str(model_path))
            self.context = load_context()
            elapsed = time.perf_counter() - started
            logging.info("模型加载完成，耗时 %.2fs", elapsed)
            self._set_state("idle", f"就绪（模型加载 {elapsed:.1f}s）")
        except Exception as exc:  # noqa: BLE001
            logging.exception("模型加载失败")
            self._set_state("error", f"模型加载失败：{exc}")

    # -- 状态 ---------------------------------------------------------------
    def _set_state(self, state: str, status: str | None = None) -> None:
        self.state = state
        # SF Symbols：单色、跟随菜单栏明暗，比 emoji 干净
        symbols = {
            "loading": ("hourglass", "⏬"),
            "idle": ("mic", "🎙"),
            "recording": ("record.circle.fill", "🔴"),
            "processing": ("waveform", "⏳"),
            "error": ("exclamationmark.triangle", "⚠️"),
        }
        symbol, fallback = symbols.get(state, ("mic", "🎙"))

        def update():
            self._set_menubar_icon(symbol, fallback)
            if status is not None:
                self.status_item.title = status

        ui_thread(update)

    def _set_menubar_icon(self, symbol: str, fallback: str) -> None:
        """用 SF Symbol 作为菜单栏图标；取不到时退回原来的文字。"""
        try:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                symbol, "Dictate"
            )
            item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
            if image is None or item is None:
                self.title = fallback
                return
            image.setTemplate_(True)
            image.setSize_((17.0, 17.0))
            item.setImage_(image)
            self.title = ""  # 清掉文字，只留图标（此时已有 image，不会回退成名字）
        except Exception:  # noqa: BLE001
            logging.exception("设置菜单栏图标失败")
            self.title = fallback

    def _error(self, message: str) -> None:
        logging.error(message)
        self._set_state("error", message)
        play_sound("Basso")
        # 2 秒后回到就绪，避免菜单栏一直停在错误态
        threading.Timer(2.0, lambda: self._set_state("idle", "就绪")).start()

    # -- 悬浮字幕 -----------------------------------------------------------
    def _hud_show(self, status: str = "● 正在听…") -> None:
        self.hud_gen += 1
        if self.hud_hide_timer is not None:
            self.hud_hide_timer.cancel()
            self.hud_hide_timer = None
        ui_thread(lambda: self.hud.show(status))

    def _hud_hide_later(self, delay: float, gen: int) -> None:
        def run():
            if self.hud_gen == gen:
                ui_thread(self.hud.hide)

        self.hud_hide_timer = threading.Timer(delay, run)
        self.hud_hide_timer.daemon = True
        self.hud_hide_timer.start()

    # -- 实时字幕 -----------------------------------------------------------
    def _on_audio_block(self, block: np.ndarray) -> None:
        q = self.stream_queue
        if q is not None:
            q.put(block)
        if self.stream_failed:
            return
        now = time.perf_counter()
        if now - self._last_level >= 0.12:
            self._last_level = now
            rms = float(np.sqrt(np.mean(block**2) + 1e-12))
            level = "▁▂▃▄▅▆▇█"[min(7, int(rms * 120))]
            ui_thread(lambda l=level: self.hud.set_status(f"● 正在听 {l}"))

    def _start_stream(self) -> None:
        self.stream_failed = False
        self.stream_queue = queue.Queue()
        self.stream_stop = threading.Event()
        self.stream_thread = threading.Thread(
            target=self._stream_worker,
            args=(self.stream_queue, self.stream_stop),
            daemon=True,
        )
        self.stream_thread.start()

    def _stop_stream(self) -> None:
        if self.stream_stop is not None:
            self.stream_stop.set()
        if self.stream_thread is not None:
            self.stream_thread.join(timeout=1.5)
        self.stream_queue = None
        self.stream_stop = None
        self.stream_thread = None

    def _stream_worker(self, q: queue.Queue, stop: threading.Event) -> None:
        from mlx_qwen3_asr import streaming as st

        chunk_len = int(0.6 * SR)
        try:
            state = st.init_streaming(
                model=self.model_path_str,
                context=self.context,
                chunk_size_sec=0.6,
                endpointing_mode="energy",
            )
        except Exception:  # noqa: BLE001
            logging.exception("实时字幕初始化失败")
            self.stream_failed = True
            ui_thread(lambda: self.hud.set_status("● 正在听（实时字幕不可用）"))
            return
        if stop.is_set():
            return

        pending = np.empty(0, dtype=np.float32)
        while not stop.is_set():
            try:
                block = q.get(timeout=0.1)
            except queue.Empty:
                continue
            pending = np.concatenate([pending, block])
            while len(pending) >= chunk_len and not stop.is_set():
                try:
                    state = st.feed_audio(pending[:chunk_len], state)
                except Exception:  # noqa: BLE001
                    logging.exception("实时字幕解码失败")
                    self.stream_failed = True
                    ui_thread(
                        lambda: self.hud.set_status("● 正在听（实时字幕不可用）")
                    )
                    return
                pending = pending[chunk_len:]
                text = to_simplified(
                    strip_context_echo(state.text, self.context)
                )
                ui_thread(lambda t=text: self.hud.set_text(t))

    # -- 系统静音 -----------------------------------------------------------
    def _silence_later(self) -> None:
        """录音开始时静音输出；等 Tink 播完再静音，免得把提示音切掉。"""
        if not self.cfg.get("mute_audio", True):
            return
        self.silence_gen += 1
        gen = self.silence_gen
        self.mute_timer = threading.Timer(0.6, lambda: self._silence_now(gen))
        self.mute_timer.daemon = True
        self.mute_timer.start()

    def _silence_now(self, gen: int) -> None:
        if gen != self.silence_gen:
            return  # 录音已经结束，别把声音又静音了
        self.mute_timer = None
        if self.silencer.mute():
            logging.info("系统输出已静音")

    def _unsilence(self) -> None:
        self.silence_gen += 1
        if self.mute_timer is not None:
            self.mute_timer.cancel()
            self.mute_timer = None
        if self.silencer.restore():
            logging.info("系统输出已恢复")

    # -- 热键 ---------------------------------------------------------------
    def on_hotkey_press(self) -> None:
        if self.state == "recording":
            return
        if self.state == "processing":
            play_sound("Basso")
            return
        if self.session is None:
            play_sound("Basso")
            self._set_state("loading", "模型还在加载，请稍等…")
            return
        self._hud_show("● 正在听…")
        self._start_stream()
        try:
            self.recorder.start()
        except Exception as exc:  # noqa: BLE001
            logging.exception("开始录音失败")
            self._stop_stream()
            ui_thread(self.hud.hide)
            self._error(f"录音失败：{exc}")
            return
        play_sound("Tink")
        self._silence_later()
        self._set_state("recording", "录音中…松开结束")
        max_sec = float(self.cfg.get("max_record_seconds", 60))
        self.record_timer = threading.Timer(max_sec, self.on_hotkey_release)
        self.record_timer.daemon = True
        self.record_timer.start()

    def on_hotkey_release(self) -> None:
        if self.state != "recording":
            return
        self._unsilence()
        if self.record_timer is not None:
            self.record_timer.cancel()
            self.record_timer = None
        try:
            pcm = self.recorder.stop()
        except Exception as exc:  # noqa: BLE001
            logging.exception("停止录音失败")
            self._stop_stream()
            ui_thread(self.hud.hide)
            self._error(f"停止录音失败：{exc}")
            return
        self._stop_stream()
        duration = len(pcm) / SR
        logging.info("录音结束，时长 %.2fs", duration)
        if duration < 0.3:
            ui_thread(self.hud.hide)
            self._set_state("idle", "录音太短，已忽略")
            return
        play_sound("Pop")
        ui_thread(lambda: self.hud.set_status("⏳ 识别中…"))
        self._set_state("processing", f"识别中（{duration:.1f}s）…")
        threading.Thread(target=self._transcribe, args=(pcm,), daemon=True).start()

    def on_cancel(self) -> None:
        if self.state != "recording":
            return
        self._unsilence()
        if self.record_timer is not None:
            self.record_timer.cancel()
            self.record_timer = None
        self.recorder.cancel()
        self._stop_stream()
        ui_thread(self.hud.hide)
        play_sound("Basso")
        self._set_state("idle", "已取消")
        logging.info("本次录音已取消")

    def begin_rebind(self, _sender) -> None:
        self.tap.begin_learning()
        self._set_state("idle", "请按一下要作为热键的键…")

    def on_bind(self, keycode: int) -> None:
        name = key_name(keycode)
        self.cfg["hotkey_keycode"] = int(keycode)
        self.cfg["hotkey_name"] = name
        save_config(self.cfg)
        self.tap.keycode = int(keycode)
        logging.info("热键已改为 %s（keycode=%s）", name, keycode)
        ui_thread(lambda: setattr(self.hotkey_item, "title", f"热键：{name}"))
        self._set_state("idle", f"热键已改为「{name}」")

    # -- 识别与上屏 ---------------------------------------------------------
    def _transcribe(self, pcm: np.ndarray) -> None:
        started = time.perf_counter()
        with self.transcribe_lock:
            try:
                result = self.session.transcribe(
                    pcm, context=self.context, verbose=False
                )
                text = to_simplified(
                    strip_context_echo(
                        getattr(result, "text", "") or "", self.context
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logging.exception("识别失败")
                ui_thread(self.hud.hide)
                ui_thread(lambda: self._error(f"识别失败：{exc}"))
                return
        elapsed = time.perf_counter() - started
        gen = self.hud_gen
        if not text:
            logging.info("识别结果为空，耗时 %.2fs", elapsed)
            ui_thread(lambda: self.hud.set_status("没有识别到内容"))
            self._hud_hide_later(0.8, gen)
            self._set_state("idle", "没有识别到内容")
            return
        logging.info("识别完成 %.2fs：%s", elapsed, text)

        ui_thread(lambda: self.hud.set_text(text))
        ui_thread(lambda: self.hud.set_status("✓ 已输入"))
        ok = paste_text(
            text,
            restore=bool(self.cfg.get("restore_clipboard", True)),
            auto_enter=bool(self.cfg.get("auto_enter", False)),
        )
        if ok:
            self._set_state("idle", f"已输入：{text[:30]}")
        else:
            ui_thread(
                lambda: self.hud.set_status("已输入（若没出现，按 Cmd+V）")
            )
            self._set_state("idle", "已输入；若没出现请按 Cmd+V（辅助功能未授权）")
        self._hud_hide_later(1.0, gen)

    # -- 菜单回调 -----------------------------------------------------------
    def toggle_auto_enter(self, sender) -> None:
        self.cfg["auto_enter"] = not bool(self.cfg.get("auto_enter"))
        sender.state = 1 if self.cfg["auto_enter"] else 0
        save_config(self.cfg)

    def toggle_restore(self, sender) -> None:
        self.cfg["restore_clipboard"] = not bool(self.cfg.get("restore_clipboard", True))
        sender.state = 1 if self.cfg["restore_clipboard"] else 0
        save_config(self.cfg)

    def toggle_mute(self, sender) -> None:
        self.cfg["mute_audio"] = not bool(self.cfg.get("mute_audio", True))
        sender.state = 1 if self.cfg["mute_audio"] else 0
        save_config(self.cfg)
        if not self.cfg["mute_audio"]:
            self._unsilence()

    def test_hud(self, _sender) -> None:
        self.hud_gen += 1
        gen = self.hud_gen

        def show():
            self.hud.show(
                "● 正在听 ▃▅▂",
                "这是一个悬浮字幕测试：Claude Code 帮我把这个接口改成流式输出，"
                "然后跑一下测试看看有没有报错。如果文字继续变多，这个悬浮窗会自动"
                "向上长高，最多显示六行，再长就只保留最新的内容。",
            )

        ui_thread(show)
        self._hud_hide_later(3.0, gen)

    def check_permissions(self, _sender) -> None:
        trusted = AXIsProcessTrusted()
        monitoring = input_monitoring_status()
        detail = (
            f"辅助功能：{'已授权' if trusted else '未授权'}\n"
            f"输入监控：{monitoring}\n\n"
        )
        if not trusted or monitoring != "已授权":
            detail += (
                "请到 系统设置 → 隐私与安全性，分别在「辅助功能」和"
                "「输入监控」里勾选 Dictate（或 Python），然后重启本程序。\n\n"
                "注意这是两个不同的列表，只开辅助功能，热键仍然收不到按键。"
            )
        rumps.alert("Dictate 权限", detail)

    def open_log(self, _sender) -> None:
        subprocess.Popen(["open", "-e", str(LOG_FILE)])

    def quit(self, _sender) -> None:
        self._unsilence()
        rumps.quit_application()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    if os_import_error is not None:
        sys.exit(f"缺少 macOS 依赖：{os_import_error}")

    setup_logging()
    cfg = load_config()
    if not CONFIG_FILE.exists():
        save_config(cfg)

    logging.info("启动 Dictate，热键 %s", key_name(int(cfg["hotkey_keycode"])))
    app = DictateApp(cfg)
    app.start()
    # 启动壳 exec 的是 Python.app，默认会被当成普通 GUI 程序，
    # 导致 Dock 里出现 Python 图标和菜单栏里的 "Python" 名字。
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
    app.run()


if __name__ == "__main__":
    main()
