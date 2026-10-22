# Dictate — 本地语音听写

按住左 Option 说话，松开后文字自动出现在当前光标处。完全离线，基于
[Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) + MLX，在 Apple Silicon 上运行。

适合在 Claude Code、终端、浏览器、聊天窗口里代替键盘输入；中英混说和技术名词（可自定义热词）表现较好。

## 系统要求

- Apple Silicon Mac（M1 及以上）
- macOS 14+
- Python 3.10+

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

./download-model.sh          # 首次下载 0.6B 模型（约 1.8GB，走 hf-mirror）
./packaging/build-app.sh     # 构建 Dictate.app
open Dictate.app
```

首次运行需要在「系统设置 → 隐私与安全性」授权：

- **麦克风**：第一次录音时弹窗
- **辅助功能**：用于模拟 Cmd+V 粘贴
- **输入监控**：用于监听全局热键

> 辅助功能和输入监控是**两个独立的列表**，两个都要打开。
> 只开辅助功能时，热键收不到按键；只开输入监控时，能识别但文字粘贴不出去。

授权后重启一次 App。如果列表里显示的是 `Python` 而不是 `Dictate`，把那一项打开即可。

## 使用

- 按住 **左 Option**（Windows 键盘上的左 Alt）说话，松开结束
- `Esc` 取消本次录音
- 说话期间自动静音系统输出（音乐、视频、网页声音），松开后恢复
- 屏幕底部出现悬浮字幕：说话时实时显示，松开后显示最终结果并自动粘贴
- 菜单栏图标：麦克风（就绪）/ 实心圆（录音中）/ 波形（识别中）/ 沙漏（加载中）/ 警告三角（出错）
- 菜单里可以：
  - 重新绑定热键（按一次任意键）
  - 切换「说完自动回车」（默认关）
  - 切换「粘贴后恢复剪贴板」（默认开）
  - 切换「听写时静音其他声音」（默认开；只静音输出，不会暂停播放器）
  - 检查权限、查看日志、测试悬浮窗

配置保存在 `dictation.json`，日志在 `logs/dictate.log`。

## 热词 / 专有名词

编辑 `hotwords.txt`，每行一个词：

```
Claude Code
cc-connect
MCP
```

这些词会作为 domain context 传给模型，改善 `claude code` 被识别成 `cloud code` 这类问题。

**不要写太多。** 热词列表过长会让流式解码复读热词，甚至把 `debug` 识别成「开发」。
只放模型真的容易听错的专有名词。

## 换更大的模型

0.6B 速度最快；追求准确率可以换 1.7B：

```bash
./download-model.sh Qwen/Qwen3-ASR-1.7B
```

然后把 `dictation.json` 里的 `model` 改成 `models/Qwen3-ASR-1.7B`，重启 App。

## 排错

- **热键没反应**：先看 `logs/dictate.log` 里的「权限状态」一行，确认辅助功能和输入监控都是「已授权」
- **重建过 App 后失灵**：ad-hoc 签名变了，macOS 会作废旧授权。到两个权限列表里用「−」删除条目，再用「+」重新添加 `Dictate.app`
- **改代码后不需要重新 build**：`dictate.py` 在 App 包外，退出重开即可；只有改 `launcher.c` / `Info.plist` 才需要 `build-app.sh`
- **密码框/授权弹窗后热键失效**：安全输入会暂停事件监听，程序会自动恢复，无需操作

## 目录

```
dictate.py            # 菜单栏 App 全部逻辑
download-model.sh     # 下载模型
hotwords.txt          # 热词表
packaging/            # App 启动壳与构建脚本
requirements.txt      # Python 依赖
dictation.json        # 运行配置（gitignore）
models/               # 本地模型（gitignore）
logs/                 # 运行日志（gitignore）
Dictate.app/          # 构建产物（gitignore）
```
