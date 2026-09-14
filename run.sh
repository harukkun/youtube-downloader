#!/bin/zsh
# 유튜브 다운로더 실행 스크립트
# 처음 실행 시 가상환경을 만들고 의존성을 설치한 뒤 서버를 띄운다.
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "⚠️  ffmpeg가 없습니다. 설치: brew install ffmpeg"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "▶ 가상환경 생성 중..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
fi

# requirements.txt가 마지막 설치 이후 바뀌었으면(또는 처음이면) 의존성을 다시 설치한다.
STAMP=.venv/.requirements.stamp
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "▶ 의존성 설치 중..."
  .venv/bin/pip install -q -r requirements.txt && touch "$STAMP"
fi

exec .venv/bin/python app.py "$@"
