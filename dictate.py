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
import contextlib
import ctypes
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import objc
import rumps

os_import_error = None
try:
    import Quartz
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSBezelStyleRounded,
        NSButton,
        NSColor,
        NSEvent,
        NSFloatingWindowLevel,
        NSFont,
        NSFontAttributeName,
        NSImage,
        NSLineBreakByWordWrapping,
        NSMakeRect,
        NSObject,
        NSOperationQueue,
        NSPanel,
        NSPasteboard,
        NSPasteboardItem,
        NSPasteboardTypeString,
        NSProgressIndicator,
        NSProgressIndicatorStyleBar,
        NSScreen,
        NSSound,
        NSString,
        NSStringDrawingUsesLineFragmentOrigin,
        NSTextAlignmentLeft,
        NSTextField,
        NSView,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorIgnoresCycle,
        NSWindowCollectionBehaviorTransient,
        NSWindowStyleMaskBorderless,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskNonactivatingPanel,
        NSWindowStyleMaskTitled,
    )
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )
except Exception as exc:
    os_import_error = exc

APP_NAME = "Dictate"
VERSION = "0.1.0"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME
LOG_DIR = Path.home() / "Library" / "Logs" / APP_NAME
MODELS_DIR = APP_SUPPORT / "models"
CONFIG_FILE = APP_SUPPORT / "config.json"
HOTWORDS_FILE = APP_SUPPORT / "hotwords.txt"
LOG_FILE = LOG_DIR / "dictate.log"

# 旧版把配置放在项目目录里；首次启动时迁移，避免用户丢配置。
LEGACY_ROOT = Path(__file__).resolve().parent
SR = 16000

DEFAULT_CONFIG = {
    "hotkey_keycode": 58,  # 左 Option
    "hotkey_name": "左 Option",
    "auto_enter": False,
    "restore_clipboard": True,
    "mute_audio": True,
    "model": "Qwen3-ASR-0.6B",
    "language": "Chinese",  # 强制输出中文，见 config_language()
    "device": None,
    "max_record_seconds": 60,
    "log_transcriptions": False,  # 默认不把识别正文写进日志
    "first_run_completed": False,
}

DEFAULT_HOTWORDS = """# 每行一个热词 / 专有名词，会作为 context 传给 Qwen3-ASR。
#
# 注意：不是越多越好。只放「模型容易听错」的专有名词；
# Python / API / GitHub 这类常见词模型本来就认识，加多了反而会干扰
# 流式解码（实测长列表会让 debug 被识别成「开发」）。
Claude Code
MCP
"""

# language 设为这些值时交给模型自动判断。
LANGUAGE_AUTO = {"", "auto", "none", "null", "detect"}

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


def ensure_app_dirs() -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def legacy_candidates(name: str) -> list[Path]:
    """旧版文件可能在工作目录或脚本目录（仓库根/旧 App 资源目录）。"""
    return [
        Path.cwd() / name,
        LEGACY_ROOT / name,
        LEGACY_ROOT.parent / name,
    ]


def migrate_legacy_files() -> None:
    """把仓库里的 dictation.json / hotwords.txt 迁移到 Application Support。"""
    ensure_app_dirs()
    if not CONFIG_FILE.exists():
        for candidate in legacy_candidates("dictation.json"):
            if candidate.is_file():
                shutil.copy2(candidate, CONFIG_FILE)
                try:
                    old = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    old.setdefault("first_run_completed", True)
                    save_config(old)
                except Exception:
                    logging.exception("迁移旧配置时补全字段失败")
                logging.info("已迁移旧配置：%s -> %s", candidate, CONFIG_FILE)
                break
        else:
            save_config(dict(DEFAULT_CONFIG))
    if not HOTWORDS_FILE.exists():
        for candidate in legacy_candidates("hotwords.txt"):
            if candidate.is_file():
                shutil.copy2(candidate, HOTWORDS_FILE)
                break
        else:
            HOTWORDS_FILE.write_text(DEFAULT_HOTWORDS, encoding="utf-8")


def resolve_model_path(value: str) -> Path:
    """把配置里的模型值解析成实际目录。

    兼容旧配置的 "models/Qwen3-ASR-0.6B"，也优先使用 Application Support
    里新下载的模型，最后才回退到仓库里的 models/。
    """
    raw = Path(os.path.expanduser(str(value)))
    if raw.is_absolute():
        return raw
    name = raw.name
    candidates = [MODELS_DIR / raw, MODELS_DIR / name]
    for legacy in (str(raw), f"models/{name}"):
        candidates.extend(legacy_candidates(legacy))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return MODELS_DIR / name


def model_ready(path: Path) -> bool:
    return (path / "config.json").is_file() and any(path.glob("*.safetensors"))


MODEL_REPO = "Qwen/Qwen3-ASR-0.6B"
MODEL_FILES = (
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "chat_template.json",
)


def model_endpoint() -> str:
    return os.environ.get("HF_ENDPOINT", "").strip() or "https://hf-mirror.com"


def _retry_download(fn, label: str, attempts: int = 3):
    """镜像偶尔返回 429/5xx；等待后重试，尊重 Retry-After。"""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            wait = min(30, 2**attempt)
            response = getattr(exc, "response", None)
            if response is not None:
                with contextlib.suppress(TypeError, ValueError):
                    wait = max(wait, min(60, int(response.headers.get("Retry-After", 0))))
            logging.warning(
                "%s失败（%s），%d 秒后重试（%d/%d）",
                label,
                exc,
                wait,
                attempt + 1,
                attempts,
            )
            time.sleep(wait)


def download_model(dest: Path, progress=None) -> None:
    """下载 Qwen3-ASR 到 dest，progress(done, total, filename) 在下载线程里调用。"""
    from huggingface_hub import HfApi, hf_hub_download
    from tqdm.auto import tqdm

    endpoint = model_endpoint()
    logging.info("下载模型 %s -> %s（endpoint=%s）", MODEL_REPO, dest, endpoint)
    api = HfApi(endpoint=endpoint)
    info = _retry_download(
        lambda: api.model_info(MODEL_REPO, files_metadata=True),
        "获取模型信息",
    )
    sizes = {s.rfilename: int(s.size or 0) for s in info.siblings}
    files = [name for name in MODEL_FILES if name in sizes]
    if "model.safetensors" not in files:
        raise RuntimeError("模型仓库里没有找到 model.safetensors")
    total = sum(sizes[name] for name in files)
    dest.mkdir(parents=True, exist_ok=True)
    done = 0
    if progress:
        progress(0, total, "")

    for filename in files:
        class _Bar(tqdm):
            def update(inner, n=1, base=done, name=filename):
                out = tqdm.update(inner, n)
                if progress:
                    try:
                        progress(min(base + (inner.n or 0), total), total, name)
                    except Exception:
                        logging.exception("更新下载进度失败")
                return out

        _retry_download(
            lambda name=filename, bar=_Bar: hf_hub_download(
                repo_id=MODEL_REPO,
                filename=name,
                local_dir=str(dest),
                endpoint=endpoint,
                tqdm_class=bar,
            ),
            f"下载 {filename}",
        )
        done += sizes[filename]
        if progress:
            progress(min(done, total), total, filename)

    if not model_ready(dest):
        raise RuntimeError("模型下载完成但校验失败")
    logging.info("模型下载完成：%s", dest)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
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
        except Exception as exc:
            logging.warning("读取配置失败，使用默认值：%s", exc)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    tmp.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(CONFIG_FILE)


def config_language(cfg: dict) -> str | None:
    """读取输出语言；None 表示让模型自动判断。

    Qwen3-ASR 按语言标签决定输出语言：自动判断时，一段中文只要被误判成
    英文，模型就会输出英文译文（流式字幕尤其容易，因为每个窗口都会重新
    判断）。中文听写默认强制 Chinese，避免整段变成英文。
    """
    value = cfg.get("language", DEFAULT_CONFIG["language"])
    if value is None:
        return None
    text = str(value).strip()
    return None if text.casefold() in LANGUAGE_AUTO else text


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
    except Exception:
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
    except Exception as exc:
        return f"检查失败：{exc}"


def request_input_monitoring() -> None:
    """触发系统的「输入监控」授权弹窗（该权限不会自动弹）。"""
    try:
        import ctypes

        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]
        iokit.IOHIDRequestAccess.restype = ctypes.c_bool
        iokit.IOHIDRequestAccess(1)  # kIOHIDRequestTypeListenEvent
    except Exception:
        logging.exception("请求输入监控权限失败")


def microphone_status() -> str:
    """查询麦克风权限（AVFoundation）。"""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        return {
            0: "未决定",
            1: "受限",
            2: "已拒绝",
            3: "已授权",
        }.get(status, f"未知({status})")
    except Exception:
        return "未知"


def request_microphone() -> None:
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda _granted: None
        )
    except Exception:
        logging.exception("请求麦克风权限失败")


NO_MIC_MESSAGE = (
    "未检测到麦克风。请连接耳机或 USB 麦克风，在「系统设置 → 声音 → 输入」"
    "确认设备出现后重试。"
)


class MicrophoneUnavailable(RuntimeError):
    """系统当前没有可用的音频输入设备。"""


def pick_input_device(devices, default_input=None, preferred=None):
    """从设备列表里选一个输入设备，返回 (index, info)；没有则 (None, None)。

    优先用配置里指定的设备；找不到就退回系统默认输入；再不行用第一个有
    输入通道的设备。系统默认输入可能是 -1，直接传给 PortAudio 会报
    「Error querying device -1」，所以这里始终返回有效编号。
    """
    inputs = [
        (index, info)
        for index, info in enumerate(devices)
        if int(info.get("max_input_channels") or 0) > 0
    ]
    if not inputs:
        return None, None
    if preferred is not None:
        for index, info in inputs:
            if index == preferred:
                return index, info
    if isinstance(default_input, int) and default_input >= 0:
        for index, info in inputs:
            if index == default_input:
                return index, info
    return inputs[0]


def open_privacy_settings(pane: str) -> None:
    """打开系统设置的隐私面板。pane: Microphone / Accessibility / ListenEvent。"""
    subprocess.Popen(
        [
            "open",
            f"x-apple.systempreferences:com.apple.preference.security?Privacy_{pane}",
        ]
    )


def ui_thread(fn) -> None:
    """把 UI 更新丢回主线程。

    Python 异常若从 block 里逃出去，PyObjC 会把它转成 ObjC 异常导致整个
    App abort（崩溃报告里就是这种）。这里统一兜住并记日志。
    """

    def wrapped():
        try:
            fn()
        except Exception:
            logging.exception("UI 回调执行失败")

    NSOperationQueue.mainQueue().addOperationWithBlock_(wrapped)


def play_sound(name: str) -> None:
    with contextlib.suppress(Exception):
        NSSound.soundNamed_(name).play()


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
        except Exception:
            logging.exception("加载 CoreAudio 失败，静音功能不可用")

    def _addr(self, selector: int, scope: int) -> AudioSilencer._Addr:
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

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


def active_screen():
    """鼠标所在屏幕；多显示器时 HUD 跟着当前屏幕走。"""
    screen = NSScreen.mainScreen()
    try:
        point = NSEvent.mouseLocation()
        for candidate in NSScreen.screens():
            frame = candidate.frame()
            if (
                frame.origin.x <= point.x < frame.origin.x + frame.size.width
                and frame.origin.y <= point.y < frame.origin.y + frame.size.height
            ):
                return candidate
    except Exception:
        logging.debug("获取当前屏幕失败", exc_info=True)
    return screen


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
        screen = active_screen().visibleFrame()
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
        measured = max(round(rect.size.height), self.LINE_HEIGHT)
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

        def callback(indata, frames, time_info, status):
            if status:
                logging.warning("音频状态：%s", status)
            block = indata[:, 0].copy()
            with self.lock:
                self.chunks.append(block)
            if self.on_block is not None:
                try:
                    self.on_block(block)
                except Exception:
                    logging.exception("处理音频块失败")

        self.stream = self._open_stream(sd, callback)
        self.stream.start()

    def _choose_input(self, sd):
        """重新查询设备列表，选一个当前可用的输入设备。"""
        try:
            devices = sd.query_devices()
        except Exception as exc:
            logging.warning("查询音频设备失败：%s", exc)
            return None, None
        try:
            default_input = sd.default.device[0]
        except Exception:
            default_input = None
        index, info = pick_input_device(devices, default_input, self.device)
        if index is None:
            logging.warning("系统当前没有可用的输入设备")
            return None, None
        if self.device is not None and index != self.device:
            logging.warning("配置的输入设备 %s 不可用，改用设备 %s", self.device, index)
        return index, info

    def _open_stream(self, sd, callback):
        """打开输入流；设备失效时自动重连，连不上时给出明确提示。

        macOS 睡眠唤醒或音频设备热插拔后，PortAudio 的设备列表可能已经
        失效，表现为 -9986（paInternalError）。先直接重试一次，再重新
        初始化 PortAudio。如果重初始化后系统里一个输入设备都没有，说明
        是麦克风没连上，继续重试没有意义，直接报「未检测到麦克风」。
        """
        index, info = self._choose_input(sd)
        if index is None:
            raise MicrophoneUnavailable(NO_MIC_MESSAGE)
        logging.info(
            "使用麦克风：%s（设备 %d，默认采样率 %s）",
            info.get("name", "未知"),
            index,
            info.get("default_samplerate", "未知"),
        )

        last_error = None
        for attempt in range(3):
            try:
                return sd.InputStream(
                    samplerate=SR,
                    channels=1,
                    dtype="float32",
                    blocksize=int(0.05 * SR),
                    device=index,
                    callback=callback,
                )
            except sd.PortAudioError as exc:
                last_error = exc
                logging.warning("打开麦克风失败（第 %d 次）：%s", attempt + 1, exc)
                if attempt == 0:
                    # 先只重查设备，不做全局重初始化：热插拔后设备编号可能变了
                    index, info = self._choose_input(sd)
                    if index is None:
                        raise MicrophoneUnavailable(NO_MIC_MESSAGE) from exc
                    logging.info("重试使用麦克风：%s（设备 %d）", info.get("name"), index)
                elif attempt == 1:
                    try:
                        sd._terminate()
                        sd._initialize()
                        logging.info("已重新初始化音频系统，正在重试")
                    except Exception:
                        logging.exception("重新初始化音频系统失败")
                    index, info = self._choose_input(sd)
                    if index is None:
                        raise MicrophoneUnavailable(NO_MIC_MESSAGE) from exc
                    logging.info("重试使用麦克风：%s（设备 %d）", info.get("name"), index)
        raise last_error  # type: ignore[misc]

    def stop(self) -> np.ndarray:
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
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

        if keycode == KEY_ESC and event_type == Quartz.kCGEventKeyDown and keycode != self.keycode:
            self.on_cancel()
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
            return
        if event_type == Quartz.kCGEventKeyDown and not self.down:
            self.down = True
            self.on_press()
        elif event_type == Quartz.kCGEventKeyUp and self.down:
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

    def _callback(self, proxy, event_type, event, refcon):
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
        except Exception:
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
    expected_change = pb.changeCount()
    time.sleep(0.02)
    post_key(KEY_V, command=True)
    if auto_enter:
        time.sleep(0.12)
        post_key(KEY_RETURN)
    if restore and trusted:
        time.sleep(0.45)
        if pb.changeCount() == expected_change:
            restore_pasteboard(pb, saved)
        else:
            # 这 0.45 秒里用户自己复制了新内容，别再把它覆盖掉。
            logging.info("剪贴板已被其他程序修改，保留新内容")
    return trusted


# ---------------------------------------------------------------------------
# 首次运行 / 设置向导
# ---------------------------------------------------------------------------

class SetupWindow(NSObject):
    """一个极简的设置窗口：模型下载 + 三项权限状态。"""

    def initWithApp_(self, app):
        self = objc.super(SetupWindow, self).init()
        if self is None:
            return None
        self.app = app
        self.window = None
        self.fields = {}
        self.downloading = False
        self.model_path = resolve_model_path(
            app.cfg.get("model", DEFAULT_CONFIG["model"])
        )
        return self

    @objc.python_method
    def _label(self, text, frame, size=13, bold=False, secondary=False, wrap=False):
        label = NSTextField.alloc().initWithFrame_(frame)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(
            NSFont.boldSystemFontOfSize_(size)
            if bold
            else NSFont.systemFontOfSize_(size)
        )
        label.setTextColor_(
            NSColor.secondaryLabelColor() if secondary else NSColor.labelColor()
        )
        if wrap:
            label.setLineBreakMode_(NSLineBreakByWordWrapping)
            label.setUsesSingleLineMode_(False)
            label.cell().setWraps_(True)
        label.setStringValue_(text)
        return label

    @objc.python_method
    def _button(self, title, frame, action, enabled=True):
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        button.setEnabled_(enabled)
        return button

    @objc.python_method
    def _build(self):
        rect = NSMakeRect(0, 0, 560, 480)
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Dictate 设置与权限")
        panel.setReleasedWhenClosed_(False)
        panel.center()

        content = NSView.alloc().initWithFrame_(rect)
        panel.setContentView_(content)

        content.addSubview_(
            self._label("Dictate 设置", NSMakeRect(28, 424, 504, 30), 22, bold=True)
        )
        content.addSubview_(
            self._label(
                "按住听写键说话，松开后文字就到光标处。听写键可在菜单栏重新绑定。"
                "模型和三项系统权限都就绪后，点「完成」。",
                NSMakeRect(28, 376, 504, 40),
                13,
                secondary=True,
                wrap=True,
            )
        )

        content.addSubview_(
            self._label("1. 语音模型", NSMakeRect(28, 340, 300, 18), 13, bold=True)
        )
        model_status = self._label(
            "检查中…", NSMakeRect(28, 312, 370, 18), 13, secondary=True
        )
        content.addSubview_(model_status)
        model_button = self._button(
            "下载模型", NSMakeRect(408, 306, 120, 28), "downloadModel:"
        )
        content.addSubview_(model_button)

        progress = NSProgressIndicator.alloc().initWithFrame_(
            NSMakeRect(28, 290, 500, 8)
        )
        progress.setStyle_(NSProgressIndicatorStyleBar)
        progress.setIndeterminate_(False)
        progress.setMinValue_(0)
        progress.setMaxValue_(100)
        progress.setDoubleValue_(0)
        progress.setHidden_(True)
        content.addSubview_(progress)

        content.addSubview_(
            self._label("2. 系统权限", NSMakeRect(28, 254, 300, 18), 13, bold=True)
        )

        rows = [
            ("麦克风", "mic_status", "请求权限", "openMicrophone:", 220),
            ("辅助功能", "acc_status", "打开设置", "openAccessibility:", 186),
            ("输入监控", "mon_status", "打开设置", "openInputMonitoring:", 152),
        ]
        for title, key, button_title, action, y in rows:
            content.addSubview_(
                self._label(title, NSMakeRect(28, y + 3, 120, 18), 13)
            )
            status = self._label(
                "检查中…", NSMakeRect(150, y + 3, 230, 18), 13, secondary=True
            )
            content.addSubview_(status)
            self.fields[key] = status
            content.addSubview_(
                self._button(button_title, NSMakeRect(390, y, 140, 28), action)
            )

        content.addSubview_(
            self._label(
                "授权后需要重启 Dictate 才会生效。辅助功能和输入监控是两个"
                "独立列表，两个都要打开。",
                NSMakeRect(28, 104, 504, 34),
                11,
                secondary=True,
                wrap=True,
            )
        )

        content.addSubview_(
            self._button("稍后", NSMakeRect(28, 40, 80, 30), "later:")
        )
        content.addSubview_(
            self._button("重新检查", NSMakeRect(120, 40, 100, 30), "recheck:")
        )
        content.addSubview_(
            self._button("重启 App", NSMakeRect(300, 40, 100, 30), "restart:")
        )
        finish = self._button("完成", NSMakeRect(420, 40, 110, 30), "finish:")
        finish.setEnabled_(False)
        content.addSubview_(finish)

        self.window = panel
        self.fields.update(
            {
                "model_status": model_status,
                "model_button": model_button,
                "progress": progress,
                "finish_button": finish,
            }
        )

    @objc.python_method
    def show(self):
        if self.window is None:
            self._build()
        self.refresh()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def refresh(self):
        model_ok = model_ready(self.model_path)
        mic = microphone_status()
        acc = "已授权" if AXIsProcessTrusted() else "未授权"
        mon = input_monitoring_status()

        self.fields["model_status"].setStringValue_(
            "已就绪" if model_ok else "未下载（约 1.8GB）"
        )
        self.fields["model_button"].setEnabled_(
            not model_ok and not self.downloading
        )
        self.fields["mic_status"].setStringValue_(mic)
        self.fields["acc_status"].setStringValue_(acc)
        self.fields["mon_status"].setStringValue_(mon)

        all_ok = (
            model_ok
            and mic in ("已授权", "未知")
            and acc == "已授权"
            and mon == "已授权"
        )
        self.fields["finish_button"].setEnabled_(all_ok)

    @objc.python_method
    def _schedule_refresh(self):
        timer = threading.Timer(1.0, lambda: ui_thread(self.refresh))
        timer.daemon = True
        timer.start()

    # -- 模型 ---------------------------------------------------------------
    def downloadModel_(self, _sender):
        if self.downloading:
            return
        if not self.app.confirm_model_download():
            return
        self.downloading = True
        self.fields["model_button"].setEnabled_(False)
        self.fields["model_button"].setTitle_("下载中…")
        self.fields["progress"].setHidden_(False)
        self.fields["progress"].setDoubleValue_(0)
        self.fields["model_status"].setStringValue_("准备下载…")
        threading.Thread(target=self._download_worker, daemon=True).start()

    @objc.python_method
    def _download_worker(self):
        last_update = [0.0]

        def on_progress(done, total, filename):
            now = time.perf_counter()
            if now - last_update[0] < 0.2 and done < total:
                return
            last_update[0] = now
            percent = (done / total * 100.0) if total else 0.0
            done_mb = done / (1024 * 1024)
            total_mb = total / (1024 * 1024)

            def update():
                self.fields["progress"].setDoubleValue_(percent)
                self.fields["model_status"].setStringValue_(
                    f"下载中 {done_mb:.0f} / {total_mb:.0f} MB  {filename}"
                )

            ui_thread(update)

        try:
            download_model(self.model_path, on_progress)
        except Exception as exc:
            logging.exception("模型下载失败")
            # 异常变量在 except 结束后会被删除，先绑定成普通局部变量再交给回调。
            error = str(exc)
            ui_thread(lambda: self._download_done(error))
        else:
            ui_thread(lambda: self._download_done(None))

    @objc.python_method
    def _download_done(self, error):
        self.downloading = False
        self.fields["model_button"].setTitle_("下载模型")
        self.fields["progress"].setHidden_(True)
        if error is not None:
            self.fields["model_status"].setStringValue_(f"下载失败：{error}")
            self.fields["model_button"].setEnabled_(True)
            return
        self.refresh()
        self.app.reload_model()

    # -- 权限 ---------------------------------------------------------------
    def openMicrophone_(self, _sender):
        if microphone_status() in ("已拒绝", "受限"):
            open_privacy_settings("Microphone")
        else:
            request_microphone()
        self._schedule_refresh()

    def openAccessibility_(self, _sender):
        open_privacy_settings("Accessibility")
        self._schedule_refresh()

    def openInputMonitoring_(self, _sender):
        open_privacy_settings("ListenEvent")
        self._schedule_refresh()

    def recheck_(self, _sender):
        self.refresh()
        self._schedule_refresh()

    def restart_(self, _sender):
        self.app.restart()

    def later_(self, _sender):
        self.window.close()

    def finish_(self, _sender):
        self.refresh()
        if not self.fields["finish_button"].isEnabled():
            rumps.alert("还有项目未完成", "请先完成模型下载和三项权限授权。")
            return
        self.app.cfg["first_run_completed"] = True
        save_config(self.app.cfg)
        self.window.close()
        self.app.reload_model()
        if rumps.alert(
            "设置完成",
            "现在重启 Dictate 让权限生效吗？",
            ok="重启",
            cancel="稍后",
        ):
            self.app.restart()


# ---------------------------------------------------------------------------
# 菜单栏 App
# ---------------------------------------------------------------------------

class DictateApp(rumps.App):
    def __init__(self, cfg: dict):
        super().__init__("Dictate", title="⏬", quit_button=None)
        self.cfg = cfg
        self.session = None
        self.context = ""
        self.language = config_language(cfg)
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
        self.setup_window = None

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
            rumps.MenuItem("设置向导…", callback=self.open_setup),
            rumps.MenuItem("检查权限…", callback=self.check_permissions),
            rumps.MenuItem("重新连接麦克风", callback=self.reconnect_microphone),
            rumps.MenuItem("编辑热词…", callback=self.edit_hotwords),
            rumps.MenuItem("测试悬浮窗", callback=self.test_hud),
            rumps.MenuItem("打开日志", callback=self.open_log),
            rumps.MenuItem(f"关于 Dictate {VERSION}", callback=self.about),
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
        mic = microphone_status()
        logging.info(
            "权限状态：麦克风=%s，辅助功能=%s，输入监控=%s",
            mic,
            "已授权" if trusted else "未授权",
            monitoring,
        )
        permissions_ok = (
            mic in ("已授权", "未知") and trusted and monitoring == "已授权"
        )
        if monitoring != "已授权":
            request_input_monitoring()
        if not trusted:
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            logging.warning("尚未获得辅助功能权限，已弹出系统授权提示")
        if not self.cfg.get("first_run_completed", False) or not model_ready(
            resolve_model_path(self.cfg.get("model", DEFAULT_CONFIG["model"]))
        ) or not permissions_ok:
            if not permissions_ok:
                logging.warning("系统权限不完整，打开设置向导")
            ui_thread(self.open_setup)

    def open_setup(self, _sender=None) -> None:
        if self.setup_window is None:
            self.setup_window = SetupWindow.alloc().initWithApp_(self)
        self.setup_window.show()

    def confirm_model_download(self) -> bool:
        model_path = resolve_model_path(
            self.cfg.get("model", DEFAULT_CONFIG["model"])
        )
        return bool(
            rumps.alert(
                "下载语音模型",
                "首次使用需要下载约 1.8GB 的 Qwen3-ASR 模型。\n\n"
                f"保存位置：{model_path}\n\n继续下载吗？",
                ok="下载",
                cancel="取消",
            )
        )

    def reload_model(self) -> None:
        if self.session is None:
            threading.Thread(target=self.load_model, daemon=True).start()

    def restart(self) -> None:
        self._unsilence()
        logging.info("重启 Dictate")
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def load_model(self) -> None:
        from mlx_qwen3_asr import Session

        model = self.cfg.get("model", DEFAULT_CONFIG["model"])
        model_path = resolve_model_path(model)
        started = time.perf_counter()
        logging.info("加载模型：%s", model_path)
        if not model_ready(model_path):
            logging.warning("模型未就绪：%s", model_path)
            self._set_state("error", "缺少语音模型，请在菜单里打开设置向导")
            return
        try:
            self.model_path_str = str(model_path)
            self.session = Session(str(model_path))
            self.context = load_context()
            elapsed = time.perf_counter() - started
            logging.info("模型加载完成，耗时 %.2fs", elapsed)
            self._set_state("idle", f"就绪（模型加载 {elapsed:.1f}s）")
        except Exception as exc:
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
        except Exception:
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
            ui_thread(lambda bar=level: self.hud.set_status(f"● 正在听 {bar}"))

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
                language=self.language,
                chunk_size_sec=0.6,
                endpointing_mode="energy",
            )
        except Exception:
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
                except Exception:
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
            if self.state == "error":
                self._set_state("error", "模型未就绪，请从菜单打开设置向导")
            else:
                self._set_state("loading", "模型还在加载，请稍等…")
            return
        # 热词可能在两次录音之间被编辑过，每次重新读一遍。
        self.context = load_context()
        self._hud_show("● 正在听…")
        self._start_stream()
        try:
            self.recorder.start()
        except MicrophoneUnavailable as exc:
            logging.warning("无法开始录音：%s", exc)
            self._stop_stream()
            ui_thread(self.hud.hide)
            self._error(str(exc))
            return
        except Exception as exc:
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
        except Exception as exc:
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
                    pcm,
                    context=self.context,
                    language=self.language,
                    verbose=False,
                )
                text = to_simplified(
                    strip_context_echo(
                        getattr(result, "text", "") or "", self.context
                    )
                )
            except Exception as exc:
                logging.exception("识别失败")
                message = f"识别失败：{exc}"
                ui_thread(self.hud.hide)
                ui_thread(lambda: self._error(message))
                return
        elapsed = time.perf_counter() - started
        gen = self.hud_gen
        if not text:
            logging.info("识别结果为空，耗时 %.2fs", elapsed)
            ui_thread(lambda: self.hud.set_status("没有识别到内容"))
            self._hud_hide_later(0.8, gen)
            self._set_state("idle", "没有识别到内容")
            return
        if self.cfg.get("log_transcriptions", False):
            logging.info("识别完成 %.2fs：%s", elapsed, text)
        else:
            logging.info("识别完成 %.2fs，%d 字", elapsed, len(text))

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
        mic = microphone_status()
        detail = (
            f"麦克风：{mic}\n"
            f"辅助功能：{'已授权' if trusted else '未授权'}\n"
            f"输入监控：{monitoring}\n\n"
        )
        if not trusted or monitoring != "已授权" or mic not in ("已授权", "未知"):
            detail += (
                "请到 系统设置 → 隐私与安全性，分别在「麦克风」「辅助功能」和"
                "「输入监控」里勾选 Dictate（从源码运行时可能是 Python），"
                "然后重启本程序。\n\n"
                "注意这是三个不同的列表，三个都要打开。"
            )
        rumps.alert("Dictate 权限", detail)

    def reconnect_microphone(self, _sender) -> None:
        """重新初始化音频系统并枚举输入设备；用于唤醒或热插拔后的异常。"""
        if self.state == "recording":
            rumps.alert("正在录音", "请先松开听写键，再重新连接麦克风。")
            return

        def run():
            try:
                import sounddevice as sd

                sd._terminate()
                sd._initialize()
                devices = sd.query_devices()
                try:
                    default_input = sd.default.device[0]
                except Exception:
                    default_input = None
                index, info = pick_input_device(
                    devices, default_input, self.cfg.get("device")
                )
            except Exception as exc:
                logging.exception("重新连接麦克风失败")
                message = f"重新连接麦克风失败：{exc}"
                ui_thread(lambda: self._set_state("error", message))
                return
            if index is None:
                ui_thread(lambda: self._error(NO_MIC_MESSAGE))
                return
            name = info.get("name", f"设备 {index}")
            logging.info("重新连接麦克风成功：%s（设备 %d）", name, index)
            ui_thread(lambda: self._set_state("idle", f"已连接麦克风：{name}"))

        threading.Thread(target=run, daemon=True).start()

    def edit_hotwords(self, _sender) -> None:
        if not HOTWORDS_FILE.exists():
            HOTWORDS_FILE.write_text(DEFAULT_HOTWORDS, encoding="utf-8")
        subprocess.Popen(["open", "-t", str(HOTWORDS_FILE)])
        rumps.alert(
            "编辑热词",
            "修改保存后，下一次录音会自动生效，不需要重启。",
        )

    def about(self, _sender) -> None:
        rumps.alert(
            f"Dictate {VERSION}",
            "按住听写键说话，松开自动粘贴。可在菜单栏重新绑定热键。\n\n"
            "识别完全离线：Qwen3-ASR + MLX，录音不会离开这台 Mac。\n\n"
            f"配置：{CONFIG_FILE}\n"
            f"日志：{LOG_FILE}\n"
            f"模型：{resolve_model_path(self.cfg.get('model', DEFAULT_CONFIG['model']))}",
        )

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

    ensure_app_dirs()
    setup_logging()
    migrate_legacy_files()
    cfg = load_config()

    logging.info(
        "启动 Dictate %s（配置 %s，日志 %s）",
        VERSION,
        CONFIG_FILE,
        LOG_FILE,
    )
    logging.info("热键 %s", key_name(int(cfg["hotkey_keycode"])))
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
