# 후킹 클립

`/hooks`에서 원본 파일(MP4/MOV), 원본 YouTube URL, SRT를 함께 입력할 수 있습니다.
파일이 있으면 파일을 자르고, URL은 자막만 보충합니다. URL만 입력하면 미리보기 때 원본을 받습니다.
한국어 수동자막, 원본 자동자막을 지원하며 음성 인식·OCR·자동 번역은 하지 않습니다.

## 사용 순서

1. 원본 연결·자막 확인. SRT가 없으면 영상 내 자막, YouTube 자막 순으로 확인합니다.
2. 자막에서 후보 찾기. 같은 자막은 이전 결과를 재사용합니다.
3. 미리보기에서 발언을 확인하고 구간·전체 시간 보정을 조정합니다.
4. 빠진 문장은 직접 입력으로 추가합니다. 이 작업은 AI를 호출하지 않습니다.
5. 여러 후보를 선택해 MP4와 TXT/JSON/ZIP을 다운로드합니다.

자동자막에는 오인식·누락이 있습니다. 후보 길이는 3–5초 권장이며, 자동 컷은 발화 경계와 다를 수 있습니다.
시간 보정은 자동 후보에만 적용됩니다. 중간 편집·배속이 다른 원본은 일정한 시간 보정으로 맞출 수 없습니다.

## 구조

- `hooks/store.py`: 원자적 JSON 저장, 재시작 복구, 임시 미디어 만료
- `hooks/subtitles.py`: 자막 파싱·정규화·분할·지문
- `hooks/analysis.py`: 제한된 후보 JSON 검증, 분석 직렬화와 디스크 캐시
- `hooks/media.py`: YouTube 자막/원본 확보, FFmpeg 추출·미리보기
- `hooks/service.py`: 작업 흐름, 수동 후보와 시간 매핑, 내보내기 메타데이터
- `hooks/routes.py`: 8MiB 분할 업로드 및 작업·파일 API
- `templates/hooks.html`, `static/hooks.js`, `static/hooks.css`: 화면

기존 AI 연결을 사용하며 후보 분석에는 자막 번호와 텍스트만 전달합니다.
모델 출력은 시작·종료 자막 번호와 평가 종류뿐입니다. 도구 사용과 자막 속 지시 수행을 금지합니다.
동일 캐시 키 분석은 직렬화 뒤 캐시를 다시 확인해 중복 호출하지 않습니다.
실패한 분할 구간만 최대 1회 재시도하며 모델을 자동 변경하지 않습니다.

저장 위치는 기본 `~/.youtube-downloader/hooks`이며 `HOOKS_DATA_DIR` 환경 변수/Flask 설정으로 바꿀 수 있습니다.
영상 업로드 한도는 Flask `HOOKS_MAX_VIDEO_BYTES`(기본 2GiB), SRT는 `HOOKS_MAX_SRT_BYTES`(5MiB)입니다.
8MiB 조각은 현재 Flask/Waitress의 12MiB 요청 제한 안에서 전송합니다.
임시 미디어는 마지막 사용 후 7일 이후 다음 정리 시 삭제합니다. 분석과 완성 결과는 보존합니다.
단일 서버 프로세스에서 동작하며 인코딩과 AI는 각각 동시 한 작업만 실행합니다.

## 검증

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python tests/hooks_browser_fixture.py
# 다른 터미널에서 Playwright가 설치된 Node 환경으로:
node tests/test_hooks_browser.js
```

브라우저 테스트는 8878 포트의 Waitress와 격리된 작업 폴더를 사용합니다. AI·YouTube를 고정 응답으로 대체합니다.
`tests/hooks_live_check.py`는 명시적으로 실행하는 실제 입력 검증 전용이며 일반 테스트에서 실행되지 않습니다.
새 기능 수정 시 먼저 고정 자료를 사용하고, 실제 AI 분석은 꼭 필요한 경우에만 수행합니다.
