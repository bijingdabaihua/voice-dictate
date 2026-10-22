#!/usr/bin/env python3
"""Qwen3-ASR 本地实时听写 demo（Apple Silicon / MLX）

用法：
    ./run-demo.sh                       # 麦克风交互听写
    ./run-demo.sh audio.wav             # 转写音频文件
    ./run-demo.sh --list-devices        # 查看输入设备
    ./run-demo.sh --duration 5          # 录 5 秒后自动结束

说明：这是验证效果的 demo，不是最终产品。录音用回车开始/结束，
最终文本会复制到剪贴板，需要你手动 Cmd+V。
"""

from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# 国内直连 HuggingFace 经常超时，默认走镜像；首次加载模型时生效
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "Qwen3-ASR-0.6B"
HOTWORDS_FILE = ROOT / "hotwords.txt"
SR = 16000


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

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
    return " ".join(dict.fromkeys(terms))  # 去重且保序


def copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    except Exception as exc:  # noqa: BLE001
        print(f"（复制到剪贴板失败：{exc}）", file=sys.stderr)


def fmt_duration(seconds: float) -> str:
    return f"{seconds:.2f}s"


def strip_context_echo(text: str, context: str) -> str:
    """流式解码的第一块偶尔会把 context 原样吐出来，这里去掉。"""
    text = (text or "").strip()
    context = (context or "").strip()
    if not text or not context:
        return text
    if text.startswith(context):
        return text[len(context) :].lstrip(" ，。,.、")
    # 模型正在逐字复读 context 的前缀，此时还没有真正的内容
    if len(text) < len(context) and context.startswith(text):
        return ""
    return text


def resolve_model(model: str) -> str:
    path = Path(model).expanduser()
    if path.exists():
        return str(path.resolve())
    if model == str(DEFAULT_MODEL):
        sys.exit(
            f"找不到模型目录：{DEFAULT_MODEL}\n"
            "下载 0.6B：\n"
            "  HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\\n"
            "  .venv/bin/hf download Qwen/Qwen3-ASR-0.6B --local-dir models/Qwen3-ASR-0.6B"
        )
    return model  # 当作 HuggingFace repo id


def input_devices():
    """返回 [(index, name, max_input_channels), ...]"""
    import sounddevice as sd

    devices = sd.query_devices()
    result = []
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            result.append((idx, dev["name"], dev["max_input_channels"]))
    return result


def print_devices() -> None:
    try:
        devs = input_devices()
    except Exception as exc:  # noqa: BLE001
        print(f"无法枚举音频设备：{exc}")
        return
    if not devs:
        print("没有找到任何麦克风输入设备。")
        print("Mac mini 没有内置麦克风，请接入耳机 / USB 麦克风后再试。")
        return
    for idx, name, ch in devs:
        print(f"  [{idx}] {name}（{ch} 通道）")


def device_arg(value: str | None):
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value  # sounddevice 也接受设备名


# ---------------------------------------------------------------------------
# 文件转写
# ---------------------------------------------------------------------------

def _canonicalize_wav(src: Path, dst: Path) -> None:
    """把 afconvert 生成的 WAVE_FORMAT_EXTENSIBLE 重写成普通 PCM WAV。

    mlx_qwen3_asr 的原生 WAV 快速路径只认 format 1/3，而 afconvert 会写
    0xFFFE（extensible），否则就要装 ffmpeg。
    """
    import wave

    data = src.read_bytes()
    if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RuntimeError("afconvert 输出不是 WAV")

    fmt = pcm = None
    i = 12
    while i + 8 <= len(data):
        chunk_id = data[i : i + 4]
        size = int.from_bytes(data[i + 4 : i + 8], "little")
        body = data[i + 8 : i + 8 + size]
        if chunk_id == b"fmt ":
            fmt = body
        elif chunk_id == b"data":
            pcm = body
        i += 8 + size + (size & 1)

    if fmt is None or pcm is None or len(fmt) < 16:
        raise RuntimeError("WAV 缺少 fmt/data 块")

    tag = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    rate = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if tag == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE
        tag = int.from_bytes(fmt[24:26], "little")

    if tag != 1 or bits != 16:
        raise RuntimeError(f"不支持的 WAV 格式：tag={tag}, bits={bits}")

    with wave.open(str(dst), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)


def to_wav(path: Path) -> Path:
    """非 wav 用 macOS 自带的 afconvert 转成 16k 单声道普通 WAV。"""
    if path.suffix.lower() == ".wav":
        return path
    tmpdir = Path(tempfile.mkdtemp(prefix="qwen-asr-"))
    raw = tmpdir / "raw.wav"
    out = tmpdir / "input.wav"
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(path), str(raw)],
        check=True,
        capture_output=True,
    )
    _canonicalize_wav(raw, out)
    return out


def transcribe_file(
    model: str,
    context: str,
    audio: Path,
    use_stream: bool = False,
    no_copy: bool = False,
) -> None:
    import numpy as np
    from mlx_qwen3_asr import load_audio, transcribe

    path = to_wav(audio)
    pcm = np.asarray(load_audio(str(path), sr=SR), dtype=np.float32)
    duration = len(pcm) / SR
    print(f"音频：{audio.name}（{duration:.2f}s）")

    if use_stream:
        from mlx_qwen3_asr import streaming as st

        state = st.init_streaming(
            model=model, context=context, chunk_size_sec=0.6, endpointing_mode="energy"
        )
        step = int(0.5 * SR)
        started = time.perf_counter()
        for i in range(0, len(pcm), step):
            state = st.feed_audio(pcm[i : i + step], state)
            print(
                f"\r\033[K  {strip_context_echo(state.text, context)}",
                end="",
                flush=True,
            )
        state = st.finish_streaming(state)
        text = strip_context_echo(state.text, context)
        elapsed = time.perf_counter() - started
        print()
    else:
        started = time.perf_counter()
        result = transcribe(pcm, model=model, context=context, verbose=False)
        text = (getattr(result, "text", "") or "").strip()
        elapsed = time.perf_counter() - started

    print(f"\n识别结果：\n{text or '（空）'}\n")
    print(
        f"耗时 {fmt_duration(elapsed)}，"
        f"RTF {elapsed / duration:.2f}（越小越快）"
    )
    if text and not no_copy:
        copy_to_clipboard(text)
        print("已复制到剪贴板。")


# ---------------------------------------------------------------------------
# 麦克风听写
# ---------------------------------------------------------------------------

def transcribe_mic(
    model: str,
    context: str,
    device,
    chunk_sec: float,
    duration: float | None,
    no_copy: bool,
    final_mode: str = "offline",
) -> None:
    import numpy as np
    import sounddevice as sd
    from mlx_qwen3_asr import streaming as st

    devs = input_devices()
    if not devs:
        print("没有找到麦克风输入设备，无法录音。\n")
        print("Mac mini 没有内置麦克风。请接入耳机 / USB 麦克风后重新运行。")
        print("也可以先用文件模式试效果：")
        print("  ./run-demo.sh /path/to/audio.wav")
        return

    print(f"模型：{Path(model).name if Path(model).exists() else model}")
    print(f"热词：{context or '（无）'}")
    if device is None:
        default_in = sd.query_devices(kind="input")
        print(f"麦克风：{default_in['name']}")

    while True:
        if duration is None:
            try:
                answer = input("\n按回车开始说话（输入 q 退出）：").strip().lower()
            except EOFError:
                break
            if answer == "q":
                break

        audio_q: queue.Queue[np.ndarray] = queue.Queue()

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                print(f"\n[音频状态] {status}", file=sys.stderr)
            audio_q.put(indata[:, 0].copy())

        stop = threading.Event()

        def wait_for_stop() -> None:
            try:
                input()
            except EOFError:
                pass
            stop.set()

        if duration is None:
            threading.Thread(target=wait_for_stop, daemon=True).start()
        else:
            timer = threading.Timer(duration, stop.set)
            timer.daemon = True
            timer.start()

        print("🎙  录音中…" + ("（再按回车结束）" if duration is None else f"（{duration}s）"))

        state = st.init_streaming(
            model=model,
            context=context,
            chunk_size_sec=chunk_sec,
            endpointing_mode="energy",
        )
        pending = np.empty(0, dtype=np.float32)
        utterance: list[np.ndarray] = []
        shown = ""
        silent_for = 0.0
        warned = False
        started = time.perf_counter()
        total_audio = 0.0

        with sd.InputStream(
            samplerate=SR,
            channels=1,
            dtype="float32",
            blocksize=int(0.1 * SR),
            device=device,
            callback=callback,
        ):
            while not stop.is_set():
                try:
                    block = audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                utterance.append(block)
                pending = np.concatenate([pending, block])
                total_audio += len(block) / SR

                rms = float(np.sqrt(np.mean(block**2) + 1e-12))
                silent_for = silent_for + 0.1 if rms < 0.002 else 0.0
                level = "▁▂▃▄▅▆▇█"[min(7, int(rms * 120))]

                if len(pending) >= chunk_sec * SR:
                    state = st.feed_audio(pending, state)
                    pending = np.empty(0, dtype=np.float32)
                    partial = strip_context_echo(state.text, context)
                    if partial != shown:
                        shown = partial
                        sys.stdout.write(f"\r\033[K  {level} {shown}")
                        sys.stdout.flush()

                if silent_for > 2.5 and not warned and not shown:
                    warned = True
                    sys.stdout.write(
                        "\r\033[K  （没有检测到声音：检查麦克风权限，或用 --device 指定设备）\n"
                    )
                    sys.stdout.flush()

        # 把停止瞬间还在队列里的音频也收进来
        while True:
            try:
                block = audio_q.get_nowait()
            except queue.Empty:
                break
            utterance.append(block)
            pending = np.concatenate([pending, block])
            total_audio += len(block) / SR

        if len(pending) > 0:
            state = st.feed_audio(pending, state)

        stop_at = time.perf_counter()
        state = st.finish_streaming(state)
        stream_text = strip_context_echo(state.text, context)
        stream_latency = time.perf_counter() - stop_at
        elapsed = time.perf_counter() - started

        # 流式负责“边说边出字”，短句再用离线模式精修一遍
        # 长句离线精修的耗时会线性增长，所以 auto 模式只对 12 秒以内的录音精修
        final = stream_text
        refine_latency = 0.0
        full_audio = np.concatenate(utterance) if utterance else np.empty(0, dtype=np.float32)
        too_short = total_audio < 0.3
        do_offline = final_mode == "offline" or (
            final_mode == "auto" and total_audio <= 12.0
        )

        if too_short:
            print("\n（录音太短，已忽略）")
        elif do_offline:
            from mlx_qwen3_asr import transcribe

            refine_at = time.perf_counter()
            result = transcribe(full_audio, model=model, context=context)
            final = strip_context_echo(getattr(result, "text", "") or "", context)
            refine_latency = time.perf_counter() - refine_at

        print()  # 结束实时那一行
        print(f"\n识别结果：\n{final or '（空）'}\n")
        print(
            f"录音 {fmt_duration(total_audio)} / 处理 {fmt_duration(elapsed)} / "
            f"流式收尾 {fmt_duration(stream_latency)}"
            + (f" / 离线精修 {fmt_duration(refine_latency)}" if refine_latency else "")
            + f" / RTF {elapsed / max(total_audio, 0.01):.2f}"
        )

        if final and not no_copy:
            copy_to_clipboard(final)
            print("已复制到剪贴板。")

        if duration is not None:
            break


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qwen3-ASR 本地实时听写 demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  ./run-demo.sh                       麦克风交互听写\n"
            "  ./run-demo.sh audio.wav             转写文件\n"
            "  ./run-demo.sh --list-devices        列出麦克风\n"
            "  ./run-demo.sh --duration 5          录 5 秒自动结束\n"
            "  ./run-demo.sh --device 3 --duration 5\n"
            "  ./run-demo.sh audio.wav --stream    用流式模式转写文件\n"
        ),
    )
    parser.add_argument("audio", nargs="?", help="要转写的音频文件（可选）")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="模型目录或 HF repo id")
    parser.add_argument("--context", default="", help="额外热词（空格分隔）")
    parser.add_argument("--device", default=None, help="输入设备 index 或名称")
    parser.add_argument("--list-devices", action="store_true", help="列出输入设备")
    parser.add_argument("--duration", type=float, default=None, help="录音秒数（默认回车型）")
    parser.add_argument("--chunk-sec", type=float, default=0.6, help="流式解码块大小（秒）")
    parser.add_argument("--stream", action="store_true", help="文件模式也走流式解码")
    parser.add_argument(
        "--final",
        choices=["auto", "offline", "stream"],
        default="auto",
        help=(
            "麦克风模式的最终结果：auto=12 秒内离线精修、更长用流式（默认）；"
            "offline=总是离线精修；stream=只用流式结果（最快）"
        ),
    )
    parser.add_argument("--no-copy", action="store_true", help="不复制到剪贴板")
    args = parser.parse_args()

    if args.list_devices:
        print_devices()
        return

    model = resolve_model(args.model)
    context = load_context(args.context)

    if args.audio:
        transcribe_file(
            model,
            context,
            Path(args.audio).expanduser(),
            args.stream,
            args.no_copy,
        )
    else:
        transcribe_mic(
            model,
            context,
            device_arg(args.device),
            args.chunk_sec,
            args.duration,
            args.no_copy,
            args.final,
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。")
