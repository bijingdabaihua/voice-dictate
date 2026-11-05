# 第三方组件

Dictate 自身以 MIT 许可证发布。构建出的 App 还包含以下第三方组件，
它们分别遵循各自的许可证；完整许可证文本随组件一起放在 App 包的
`Contents/Resources/site-packages/*.dist-info/` 目录中。

| 组件 | 用途 | 许可证 | 项目地址 |
| --- | --- | --- | --- |
| Qwen3-ASR 模型权重 | 语音识别模型 | Apache-2.0 | <https://huggingface.co/Qwen/Qwen3-ASR-0.6B> |
| mlx-qwen3-asr | MLX 推理实现 | Apache-2.0 | <https://github.com/moona3k/mlx-qwen3-asr> |
| MLX / mlx-metal | Apple Silicon 机器学习框架 | MIT | <https://github.com/ml-explore/mlx> |
| PyObjC | macOS 原生 API 绑定 | MIT | <https://github.com/ronaldoussoren/pyobjc> |
| rumps | 菜单栏 App 封装 | BSD-3-Clause | <https://github.com/jaredks/rumps> |
| sounddevice | 音频输入 | MIT | <https://github.com/spatialaudio/python-sounddevice> |
| NumPy | 音频数组处理 | BSD-3-Clause | <https://numpy.org> |
| huggingface_hub | 模型下载 | Apache-2.0 | <https://github.com/huggingface/huggingface_hub> |
| tqdm | 下载进度条 | MPL-2.0 AND MIT | <https://github.com/tqdm/tqdm> |
| CPython（python-build-standalone） | 内置 Python 运行时 | PSF-2.0 | <https://github.com/astral-sh/python-build-standalone> |

模型权重在首次启动时由用户按需下载，不随 App 一起分发。
