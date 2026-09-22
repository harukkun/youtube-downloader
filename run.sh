#!/bin/zsh
# 유튜브 다운로더 실행 스크립트
# 처음 실행 시 가상환경을 만들고 의존성을 설치한 뒤 서버를 띄운다.
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "⚠️  ffmpeg가 없습니다. 설치: brew install ffmpeg"
  exit 1
fi

# 유튜브가 일부 영상에서 요구하는 JS 챌린지 해결에 쓴다. 없어도 대부분의 영상은 받아지므로 경고만 한다.
if ! command -v deno >/dev/null 2>&1 && ! command -v node >/dev/null 2>&1 && ! command -v bun >/dev/null 2>&1; then
  echo "⚠️  deno/node가 없습니다. 일부 영상 조회가 실패할 수 있습니다. 설치: brew install deno"
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
  if .venv/bin/pip install -q -r requirements.txt; then touch "$STAMP"; else echo "⚠️  의존성 설치에 실패했습니다. 네트워크를 확인한 뒤 run.sh 를 다시 실행하세요. (유튜브 업로드 기능은 비활성화됩니다)"; fi
fi

exec .venv/bin/python app.py "$@"
