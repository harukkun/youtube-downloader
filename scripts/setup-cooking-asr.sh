#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3.11 -m venv .venv-asr
.venv-asr/bin/python -m pip install 'mlx-whisper==0.4.3'
printf '%s\n' '음성 인식 환경 준비 완료. 최초 인식 시 Whisper small 모델을 다운로드합니다.'
