#!/usr/bin/env bash
# 下载 Qwen3-ASR 模型（默认 0.6B）
# 用法：./download-model.sh [Qwen/Qwen3-ASR-1.7B]
set -euo pipefail
cd "$(dirname "$0")"

REPO="${1:-Qwen/Qwen3-ASR-0.6B}"
DIR="models/$(basename "$REPO")"

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET=1

echo "下载 $REPO -> $DIR"
.venv/bin/hf download "$REPO" --local-dir "$DIR"
echo "完成：$DIR"
