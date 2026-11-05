# Dictate

**按住左 Option 说话，松开就把文字粘贴到光标处。** 识别完全在本地，录音不上传。

基于 [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) + [MLX](https://github.com/ml-explore/mlx)，
在 Apple Silicon 上离线完成语音识别。适合在 Claude Code、终端、浏览器、聊天窗口里代替键盘输入；
中英混说和技术名词（可自定义热词）表现较好。

https://github.com/bijingdabaihua/voice-dictate

## 特性

- **按住即说，松开即写**：左 Option 全局热键，不需要切换窗口
- **本地识别**：模型跑在本机 GPU 上，录音和文字都不离开电脑
- **流式字幕**：说话时屏幕底部实时显示识别结果，松开后自动粘贴
- **中英混说**：默认强制中文输出，英文技术名词保持原样
- **热词表**：把 `Claude Code`、`MCP` 这类容易听错的词交给模型
- **听写时静音**：自动静音系统输出，避免音乐和视频盖住说话声
- **不弄脏剪贴板**：粘贴后默认恢复原来的剪贴板内容
- **原生小工具**：菜单栏 App，无 Dock 图标，无账号，无订阅

## 系统要求

- Apple Silicon Mac（M1 / M2 / M3 / M4 及以上）
- macOS 14 Sonoma 或更新版本
- 首次启动需要联网下载模型（约 1.8 GB，默认走 hf-mirror 镜像）
- 磁盘空间：App 约 340 MB，模型约 2 GB

Intel Mac 和 Windows 暂不支持。

## 安装

1. 到 [Releases](https://github.com/bijingdabaihua/voice-dictate/releases/latest) 下载
   `Dictate-0.1.0.dmg`
2. 打开 DMG，把 **Dictate** 拖进 **Applications**
3. 从「应用程序」启动 Dictate

> **首次打开被 macOS 拦截？**
> 当前版本没有 Apple Developer ID 公证。到「系统设置 → 隐私与安全性」，
> 在页面底部点「仍要打开」；或者在 Finder 里右键 App →「打开」。
> 只需要做一次。

国内访问 GitHub 较慢时，可以使用 Gitee 镜像：<https://gitee.com/bijingdabaihua/voice-dictate>（如未同步，请以 GitHub 为准）。

## 首次启动

App 会打开一个设置向导，按顺序完成三件事：

1. **下载模型**：点「下载模型」，等待进度条走完（约 1.8 GB）
2. **授权麦克风**：用于录音
3. **授权辅助功能 + 输入监控**：辅助功能用于模拟 Cmd+V 粘贴，输入监控用于接收全局热键

> 辅助功能和输入监控是 macOS 里**两个独立的列表**，两个都要打开。
> 只开辅助功能时收不到热键；只开输入监控时能识别但粘贴不出去。

授权完成后点「完成」。如果系统要求重启 App，菜单里有「重启 App」。

模型和配置都在 `~/Library/Application Support/Dictate/`，日志在
`~/Library/Logs/Dictate/dictate.log`。

## 使用

- 按住 **左 Option**（Windows 键盘上的左 Alt）说话，松开结束并粘贴
- 录音时按 `Esc` 取消本次听写
- 菜单栏图标状态：麦克风（就绪）/ 实心圆（录音中）/ 波形（识别中）/ 沙漏（加载中）/ 警告（出错）
- 菜单里可以重新绑定热键、开关「说完自动回车」、开关「粘贴后恢复剪贴板」、
  开关「听写时静音其他声音」、编辑热词、打开日志、重新运行设置向导

识别结果会直接粘贴到当前光标处。如果当前窗口不支持 Cmd+V，文字仍会留在剪贴板里。

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

## 隐私

- 录音只在内存里处理，不写入磁盘，不上传
- 识别完全在本机完成；只有首次下载模型时会访问 Hugging Face 镜像
- 日志默认**不记录**识别正文，只记录时长、字符数和错误信息
- 需要排查问题时，可以在 `config.json` 里临时打开 `log_transcriptions`，
  记得用完关掉

## 常见问题

**热键没反应**

打开菜单「检查权限」，确认麦克风、辅助功能、输入监控都显示「已授权」。
辅助功能和输入监控是两个列表，别只开一个。

**更新或重新构建后权限失效**

ad-hoc 签名的 App 每次构建签名都会变化，macOS 会作废旧授权。到
「系统设置 → 隐私与安全性」里，把旧的 Dictate 条目用「−」删除，再重新添加。
发布版升级时也可能需要重新授权。

**麦克风突然录不到声音**

长时间运行、休眠唤醒或切换音频设备后，音频子系统可能进入异常状态。
当前版本会自动重连一次；如果仍然失败，菜单「重启 App」。

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

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
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
