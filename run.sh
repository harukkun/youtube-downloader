#!/bin/zsh
# 유튜브 다운로더 실행 스크립트
# 처음 실행 시 가상환경을 만들고 의존성을 설치한 뒤 서버를 띄운다.
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "⚠️  ffmpeg가 없습니다. 설치: brew install ffmpeg"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "▶ 가상환경 생성 및 의존성 설치 중..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/python app.py "$@"
