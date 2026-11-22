"""不依赖麦克风、模型和 GUI 的核心逻辑测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

import dictate


def test_config_language_defaults_to_chinese():
    assert dictate.config_language({}) == "Chinese"


def test_config_language_auto_variants_mean_none():
    for value in (None, "", "auto", "AUTO", "none", " null ", "detect"):
        assert dictate.config_language({"language": value}) is None


def test_config_language_keeps_explicit_value():
    assert dictate.config_language({"language": " English "}) == "English"


def test_strip_context_echo_removes_prefix():
    assert dictate.strip_context_echo("Claude Code 今天天气不错", "Claude Code") == "今天天气不错"


def test_strip_context_echo_drops_leading_punctuation():
    assert dictate.strip_context_echo("Claude Code，你好", "Claude Code") == "你好"


def test_strip_context_echo_drops_partial_context():
    assert dictate.strip_context_echo("Claude", "Claude Code") == ""


def test_strip_context_echo_keeps_unrelated_text():
    assert dictate.strip_context_echo("你好", "Claude Code") == "你好"
    assert dictate.strip_context_echo("", "Claude Code") == ""
    assert dictate.strip_context_echo("你好", "") == "你好"


def test_key_name():
    assert dictate.key_name(58) == "左 Option"
    assert dictate.key_name(9999) == "keycode 9999"


def test_hud_clip_trims_and_keeps_tail():
    assert dictate.HUD._clip("  你好  ") == "你好"
    long_text = "前" + "中" * 300 + "后"
    clipped = dictate.HUD._clip(long_text)
    assert clipped.startswith("…")
    assert clipped.endswith("后")
    assert len(clipped) == dictate.HUD.MAX_CHARS + 1


def test_pick_input_device_skips_output_only_devices():
    devices = [
        {"name": "扬声器", "max_input_channels": 0},
        {"name": "EarPods Microphone", "max_input_channels": 1},
    ]
    index, info = dictate.pick_input_device(devices)
    assert index == 1
    assert info["name"] == "EarPods Microphone"


def test_pick_input_device_none_when_no_input_hardware():
    devices = [{"name": "扬声器", "max_input_channels": 0}]
    assert dictate.pick_input_device(devices) == (None, None)
    assert dictate.pick_input_device([]) == (None, None)


def test_pick_input_device_prefers_configured_device():
    devices = [
        {"name": "内置麦克风", "max_input_channels": 1},
        {"name": "USB 麦克风", "max_input_channels": 2},
    ]
    index, info = dictate.pick_input_device(devices, 0, preferred=1)
    assert index == 1
    assert info["name"] == "USB 麦克风"


def test_pick_input_device_falls_back_when_configured_device_missing():
    devices = [
        {"name": "扬声器", "max_input_channels": 0},
        {"name": "内置麦克风", "max_input_channels": 1},
    ]
    index, _ = dictate.pick_input_device(devices, -1, preferred=9)
    assert index == 1


def test_pick_input_device_falls_back_when_default_input_is_minus_one():
    devices = [
        {"name": "扬声器", "max_input_channels": 0},
        {"name": "USB 麦克风", "max_input_channels": 1},
    ]
    index, _ = dictate.pick_input_device(devices, -1)
    assert index == 1


def test_retry_download_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(dictate.time, "sleep", lambda _seconds: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("busy")
        return "ok"

    assert dictate._retry_download(flaky, "test") == "ok"
    assert len(calls) == 3


def test_retry_download_raises_after_last_attempt(monkeypatch):
    monkeypatch.setattr(dictate.time, "sleep", lambda _seconds: None)
    calls = []

    def always_fails():
        calls.append(1)
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        dictate._retry_download(always_fails, "test")
    assert len(calls) == 3


def test_model_ready(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    assert not dictate.model_ready(model)
    (model / "config.json").write_text("{}", encoding="utf-8")
    assert not dictate.model_ready(model)
    (model / "model.safetensors").write_bytes(b"")
    assert dictate.model_ready(model)


def test_resolve_model_path_prefers_app_support(tmp_path: Path, monkeypatch):
    models_dir = tmp_path / "support" / "models"
    target = models_dir / "Qwen3-ASR-0.6B"
    target.mkdir(parents=True)
    monkeypatch.setattr(dictate, "MODELS_DIR", models_dir)
    monkeypatch.setattr(dictate, "LEGACY_ROOT", tmp_path / "app")
    monkeypatch.chdir(tmp_path)

    assert dictate.resolve_model_path("Qwen3-ASR-0.6B") == target.resolve()
    assert dictate.resolve_model_path("models/Qwen3-ASR-0.6B") == target.resolve()


def test_resolve_model_path_falls_back_to_legacy_repo(tmp_path: Path, monkeypatch):
    legacy = tmp_path / "repo" / "models" / "Qwen3-ASR-0.6B"
    legacy.mkdir(parents=True)
    monkeypatch.setattr(dictate, "MODELS_DIR", tmp_path / "support" / "models")
    monkeypatch.setattr(dictate, "LEGACY_ROOT", tmp_path / "repo")
    monkeypatch.chdir(tmp_path / "repo")

    assert dictate.resolve_model_path("models/Qwen3-ASR-0.6B") == legacy.resolve()


def test_resolve_model_path_absolute_passthrough(tmp_path: Path):
    absolute = tmp_path / "custom-model"
    assert dictate.resolve_model_path(str(absolute)) == absolute


def test_resolve_model_path_returns_download_target_when_missing(tmp_path: Path, monkeypatch):
    models_dir = tmp_path / "support" / "models"
    monkeypatch.setattr(dictate, "MODELS_DIR", models_dir)
    monkeypatch.setattr(dictate, "LEGACY_ROOT", tmp_path / "app")
    monkeypatch.chdir(tmp_path)

    assert dictate.resolve_model_path("Qwen3-ASR-1.7B") == models_dir / "Qwen3-ASR-1.7B"


def test_escape_cancels_while_default_modifier_is_down(monkeypatch):
    events = []
    tap = dictate.HotkeyTap(
        lambda: events.append("press"), lambda: events.append("release"),
        lambda: events.append("cancel"), lambda code: None,
    )
    quartz = dictate.Quartz
    monkeypatch.setattr(quartz, "CGEventGetIntegerValueField", lambda event, field: event[0])
    monkeypatch.setattr(quartz, "CGEventGetFlags", lambda event: event[1])
    tap._handle(quartz.kCGEventFlagsChanged, (58, dictate.MODIFIER_KEYS[58][1]))
    tap._handle(quartz.kCGEventKeyDown, (dictate.KEY_ESC, 0))
    tap._handle(quartz.kCGEventFlagsChanged, (58, 0))
    assert events == ["press", "cancel", "release"]
