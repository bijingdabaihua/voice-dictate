# Dictate

**Mac 上的本地听写键。按住自己选的键说话，松手，文字就到光标处。**

给 Claude Code 或其他终端工具提需求时，可以直接说：“先别改代码，帮我看看为什么这个 API 会发两次请求。”Dictate 会在悬浮窗显示识别过程，松键后把结果粘贴到当前输入框。默认由你检查文字、按回车发送。

![Dictate 原生悬浮窗显示一次本机识别结果](assets/dictation-preview.png)

**[下载 v0.1.0 安装包（DMG）](https://github.com/bijingdabaihua/voice-dictate/releases/download/v0.1.0/Dictate-0.1.0.dmg)** · [最新版本和校验文件](https://github.com/bijingdabaihua/voice-dictate/releases/latest) · [报告问题](https://github.com/bijingdabaihua/voice-dictate/issues)

适用于 **Apple Silicon Mac（M1 或更新）和 macOS 15 或更新版本**。安装包自带运行环境。首次联网下载约 1.8 GB 的 Qwen3-ASR 模型，之后由 MLX 在本机识别。

## 第一次使用

1. 打开 DMG，把 **Dictate** 拖进「应用程序」，从那里启动。当前版本未经 Apple 公证；若被拦截，可在「系统设置 → 隐私与安全性」中找到本次启动记录，选择「仍要打开」。
2. 按向导下载模型，并开启 **麦克风**（录音）、**输入监控**（监听听写键）、**辅助功能**（粘贴文字）。如果提示重启，退出并重新打开 Dictate。
3. 把光标放进终端、编辑器或聊天输入框。默认按住 **左 Option** 说话，松开后查看结果。

### 换成自己的听写键

点击菜单栏的 Dictate 图标 → **重新绑定热键…** → 按一下想用的键。之后按住这个键开始听写，松开结束，选择会自动保存。支持单个普通键或常用修饰键，建议选不常用的一颗：普通键仍可能被当前应用接收。

### 日常使用

- **取消本次听写**：按住听写键时按 Esc（听写键本身设为 Esc 时除外）。
- **中英混说**：在菜单里点「编辑热词…」，加入容易听错的项目名或缩写，例如 Claude Code、MCP。
- **粘贴与发送**：粘贴后默认恢复先前的剪贴板。菜单里可单独开启「说完自动回车」。
- **隐私**：录音在内存中处理，Dictate 不上传音频，日志默认不写识别正文。

## 配置与热词

配置文件：`~/Library/Application Support/Dictate/config.json`
热词文件：`~/Library/Application Support/Dictate/hotwords.txt`（菜单「编辑热词…」可直接打开）

常用配置项：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `hotkey_keycode` | `58` | 触发热键的 keycode，默认左 Option |
| `language` | `"Chinese"` | `"Chinese"` 强制中文；`"English"` 强制英文；`"auto"` 交给模型判断 |
| `auto_enter` | `false` | 粘贴后自动按回车 |
| `restore_clipboard` | `true` | 粘贴后恢复原剪贴板内容 |
| `mute_audio` | `true` | 录音时静音系统输出 |
| `model` | `"Qwen3-ASR-0.6B"` | 模型目录名，可换成 1.7B |
| `device` | `null` | 输入设备编号；`null` 使用系统默认麦克风 |
| `max_record_seconds` | `60` | 单次录音上限 |

热词每行一个，只放**模型真的容易听错**的专有名词：

```
Claude Code
MCP
cc-connect
```

热词不是越多越好。列表过长可能让流式解码复读热词，甚至把常见词识别错。

### 想听写英文

把 `language` 改成 `"English"`，重启 App。中英混说时建议保持 `"Chinese"`，
英文技术名词一般会原样保留。

### 换更大的模型

0.6B 速度最快。追求准确率可以下载 1.7B：

```bash
HF_ENDPOINT=https://hf-mirror.com .venv/bin/hf download \
  Qwen/Qwen3-ASR-1.7B --local-dir \
  "$HOME/Library/Application Support/Dictate/models/Qwen3-ASR-1.7B"
```

然后把 `config.json` 里的 `model` 改成 `"Qwen3-ASR-1.7B"`，重启 App。

## 常见问题

**热键没反应**

打开菜单「检查权限」，确认麦克风、辅助功能、输入监控都显示「已授权」。
辅助功能和输入监控是两个列表，别只开一个。

**为什么权限老是失效？**

当前安装包使用临时签名。更新或替换应用后，macOS 可能要求重新授权。请从「应用程序」启动 Dictate，用菜单里的「检查权限」查看缺少哪一项；必要时在系统设置中移除旧条目，再为当前应用授权。

**麦克风突然录不到声音**

先确认麦克风真的连上了：打开「系统设置 → 声音 → 输入」，看设备列表里
有没有你的耳机或 USB 麦克风。Mac mini 没有内置麦克风，需要外接。

如果设备在系统里正常显示，但 App 仍提示录音失败：菜单里点
「重新连接麦克风」，它会重新初始化音频系统。仍然不行时再点「重启 App」。
长时间运行、休眠唤醒或热插拔音频设备后，音频子系统可能进入异常状态。

**第一次识别很慢**

模型首次加载需要几秒到十几秒，之后会常驻内存。菜单栏显示沙漏时请稍等。

**公司电脑 / 受管 Mac 无法授权**

辅助功能和输入监控可能被 MDM 策略禁用，需要联系 IT。

**卸载**

退出 App，删除 `/Applications/Dictate.app`，再按需删除
`~/Library/Application Support/Dictate/` 和 `~/Library/Logs/Dictate/`。

## 从源码运行

```bash
git clone https://github.com/bijingdabaihua/voice-dictate.git
cd voice-dictate

python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python dictate.py
```

首次运行同样会打开设置向导并下载模型。开发时直接改 `dictate.py` 后重启进程即可。

```bash
make test    # pytest
make lint    # ruff
make run     # 直接用 .venv 运行
```

### 构建 App

```bash
./packaging/build-app.sh          # 自包含 App + DMG，输出到 dist/
SKIP_DMG=1 ./packaging/build-app.sh   # 只构建 .app
./packaging/build-dev-app.sh      # 开发用 App，依赖仓库 .venv
```

自包含构建会下载可移植 Python 和依赖，首次较慢；之后有缓存。
有 Developer ID 时可以这样签名：

```bash
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
  ./packaging/build-app.sh
```

## 项目结构

```
dictate.py                 # 菜单栏 App 主体（录音、识别、粘贴、HUD、设置向导）
assets/make-icon.py        # 生成单色 App 图标
packaging/launcher.c       # App 启动壳
packaging/Info.plist       # Bundle 信息与权限说明
packaging/build-app.sh     # 自包含构建 + DMG
packaging/build-dev-app.sh # 开发用构建
download-model.sh          # 从命令行下载模型
requirements.txt           # 开发依赖
requirements-app.txt       # App 内打包的直接依赖
tests/                     # 核心逻辑测试
docs/video/                # B 站介绍视频脚本、分镜、字幕
docs/product-review.md     # 发布前的产品评审与路线图
```

## 致谢

- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — 语音识别模型
- [mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr) — MLX 推理实现
- [MLX](https://github.com/ml-explore/mlx) — Apple Silicon 机器学习框架
- [rumps](https://github.com/jaredks/rumps) — 菜单栏 App 封装
- [PyObjC](https://github.com/ronaldoussoren/pyobjc) — macOS 原生 API 绑定

第三方组件的许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可证

[MIT](LICENSE) © 2026 bijingdabaihua
