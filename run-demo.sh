#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"

exec .venv/bin/python demo.py "$@"
