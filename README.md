# Dictate — 本地语音听写

按住右 Option 说话，松开后文字自动出现在当前光标处。完全离线，基于 [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) + MLX，在 Apple Silicon 上运行。

适合在 Claude Code、终端、浏览器、聊天窗口里代替键盘输入；中英混说和技术名词（可自定义热词）表现较好。

## 安装

需要 Apple Silicon Mac（M1 及以上）、macOS 14+、Python 3.10+。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

./download-model.sh          # 首次下载 0.6B 模型（约 1.8GB，走 hf-mirror）
./packaging/build-app.sh     # 构建 Dictate.app
open Dictate.app
```

首次运行需要在「系统设置 → 隐私与安全性」授权：

- **麦克风**：第一次按住热键时弹窗
- **辅助功能**：用于模拟 Cmd+V 自动粘贴
- **输入监控**：用于监听全局热键

> 如果权限列表里显示的是 Python 而不是 Dictate，把那一项打开即可。
> 授权后如果热键没反应，退出 App 再打开一次。

## 使用

- 按住 **右 Option**（Windows 键盘的右 Alt）说话，松开结束
- 屏幕底部会出现悬浮字幕：说话时实时显示，松开后显示最终结果并自动粘贴
- `Esc` 取消本次录音
- 菜单栏图标：`🎙` 就绪 / `🔴` 录音 / `⏳` 识别中 / `⚠️` 出错
- 菜单里可以：
  - 重新绑定热键（按一次任意键）
  - 切换「说完自动回车」（默认关）
  - 切换「粘贴后恢复剪贴板」（默认开）
  - 检查权限、查看日志、测试悬浮窗

配置保存在 `dictation.json`，日志在 `logs/dictate.log`。

## 命令行 demo

不启动菜单栏 App，直接验证模型效果：

```bash
# 麦克风交互听写（回车开始，再回车结束）
./run-demo.sh

# 转写音频文件（wav 直接读，m4a/mp3 会用系统 afconvert 转换）
./run-demo.sh samples/中英混说.wav
./run-demo.sh samples/中英混说.wav --stream   # 流式模式

# 查看输入设备
./run-demo.sh --list-devices
```

最终识别结果会复制到剪贴板。

## 热词 / 专有名词

编辑 `hotwords.txt`，每行一个词：

```
Claude Code
cc-connect
MCP
```

这些词会作为 domain context 传给模型，改善 `claude code` 被识别成 `cloud code` 这类问题。
也可以临时追加：`./run-demo.sh --context "Kubernetes MLOps RAG"`。

**不要写太多**。热词列表过长（十几个常见英文词）会让流式解码复读热词，甚至把 `debug` 识别成「开发」。只放模型真的容易听错的专有名词。

## 换更大的模型

0.6B 速度最快；追求准确率可以换 1.7B：

```bash
./download-model.sh Qwen/Qwen3-ASR-1.7B
./run-demo.sh --model models/Qwen3-ASR-1.7B
```

App 的模型路径在 `dictation.json` 的 `model` 字段里改。

## 当前限制

- 录音用按住/松开，还不是「按一下持续」模式
- 不做 LLM 润色、不自动发送（自动回车是可选开关）
- 0.6B 在嘈杂环境、重口音下不如云端大模型；热词能缓解专业术语问题
- 蓝牙耳机切到通话模式时，开头约 1 秒可能丢音
- 长文本悬浮窗最多显示 6 行，超出后保留最新内容（最终粘贴仍是完整结果）

## 目录

```
dictate.py            # 菜单栏 App（全局热键 / 悬浮字幕 / 自动粘贴）
demo.py               # 命令行 demo
run-dictate.sh        # 源码方式启动 App
run-demo.sh           # 命令行 demo 启动脚本
download-model.sh     # 下载模型
hotwords.txt          # 热词表
samples/              # 示例音频
packaging/            # App 启动壳与构建脚本
models/               # 本地模型（gitignore）
.venv/                # Python 依赖（gitignore）
logs/                 # 运行日志（gitignore）
```
