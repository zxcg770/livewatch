#!/usr/bin/env bash
# 下载 SenseVoice（int8 量化，约 163MB）和 Silero VAD 模型到 models/
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models
BASE=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
NAME=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17

[ -f models/silero_vad.onnx ] || curl -L -o models/silero_vad.onnx "$BASE/silero_vad.onnx"
[ -d "models/$NAME" ] || curl -L "$BASE/$NAME.tar.bz2" | tar xj -C models
echo "模型就绪：$(ls models)"
