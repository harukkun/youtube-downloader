# 업로드 프로세스 최종 단계에 YouTube Data API 업로드 연동 (1차: 비공개 전용)

## Context

업로드 프로세스(`/upload-process`)는 제목·링크·설명글·썸네일을 모아 현황판 시트에 저장하고 상태를 `✅ 업로드 완료`로 바꾸지만 실제 유튜브 게시는 하지 않는다. 영상 파일 개념도 없고 Google OAuth 코드도 없다. 목표는 최종 확인 단계에서 영상 파일을 고르면 **YouTube Data API v3로 비공개 업로드하고, 성공 시 시트 제출에 유튜브 체크·게시 링크를 원자적으로 함께 기록**하는 것.

사용자 결정: 파일 선택기는 최종 확인 단계 / 시트 기록은 Code.gs v15 원자 기록 / `google-api-python-client` 사용 / 1차는 비공개 전용, 공개·예약은 감사 통과 후 2차 / 썸네일은 시도(1080×1920 JPEG는 쇼츠 규격에 맞음).

외부 제약(조사 확정):
- 미감사 프로젝트로 올린 영상은 비공개로 잠기고 이의신청 불가, 재업로드 필요 → **감사 신청을 구현과 병행해 먼저 시작**. 승인 전에는 "테스트 업로드"만 하고 시트에는 제출하지 않는다.
- 할당량: 업로드 1건 ≈ 100 단위, videos.insert 전용 버킷. 제목 ≤100자, 설명 ≤5000바이트, 둘 다 `<>` 금지.
- Google OAuth 웹 클라이언트의 리디렉션 URI는 공인 IP를 허용하지 않는다. `run-public.sh:19`는 `APP_PUBLIC_ORIGIN=https://<IP>` → **Google 연결은 로컬(127.0.0.1)에서만** 하고 저장된 refresh token을 공개 모드가 공유한다.
- OAuth 앱이 "테스트" 상태면 refresh token 7일 만료 → Workspace 채널이면 내부(Internal), 개인 채널이면 외부 + 프로덕션 게시.

## 3차 재검토 반영 요약 (보안·운영/UX·단순화)

1. **시트에 쓰는 URL은 서버가 결정**: 클라이언트는 `youtube_job_id`만 보내고, 서버가 잡 상태(`done`, 같은 `item_id`·`connection`)에서 `video_id`를 읽어 `https://youtu.be/<id>`를 만든다. 브라우저가 보낸 URL을 신뢰하지 않는다.
2. **전송·저장은 편집 헬퍼 인프라 재사용**: 영상은 기존 `/api/hooks/uploads` 청크 API로 올리고(`asset_id`), 잡은 공유 hooks `Store`에 `feature='youtube-upload'`로 저장(`cooking_audio`가 쓰는 방식). 별도 저장소·청크 코드·IndexedDB 마이그레이션 없음.
3. **서버가 진실의 원천**: `GET /api/youtube/jobs?item_id=&connection=`으로 항목 선택 시 기존 잡을 조회. `restartDraft`·리로드·시트 충돌 뒤에도 URL이 남고, 설정 패널의 "유튜브 업로드 기록" 목록이 고아 잡의 복구 창구.
4. **감사 상태 토글 `audit_passed`(기본 false)**: false면 업로드는 되지만 시트 제출은 하지 않는 "테스트 업로드"로 동작하고 빨간 배너를 띄운다. true면 전체 흐름. 감사 전 업로드가 시트를 오염시키지 않는다.
5. **자격증명은 별도 파일 0600**: `~/.youtube-downloader/youtube_credentials.json`, 자체 lock. config.json에는 비밀이 아닌 `youtube_audit_passed`만.
6. OAuth: state는 `flask.session`, `/oauth/start`는 POST로 URL 반환, `fetch_token(code=)`만 사용(`OAUTHLIB_INSECURE_TRANSPORT` 금지), revoke는 POST body, 잡이 busy면 disconnect 409.
7. 로컬 모드 보호: `access_control.py`에 비밀번호 없을 때도 Host 검사(`HOST:PORT`, `127.0.0.1:PORT`, `localhost:PORT`; HOST가 `0.0.0.0`이면 생략)와 `/api/youtube/*` non-GET Origin 검사 추가.
8. 업로드 전 Apps Script 버전 확인: `POST /jobs`가 전체 흐름(`audit_passed=true`)일 때 `ping` 버전 ≥15가 아니면 409 `upgrade_required` → 영상 생성 전에 차단.
9. 재시도 로직은 라이브러리에 맡김: `next_chunk(num_retries=5)`. 중복 탐지(재생목록 조회)는 제거하고 99% 이후 중단 시 "Studio에서 영상 존재 확인" 메시지로 대체.
10. UX: 업로드 중 전체 오버레이 대신 4단계 안 진행 카드(취소 버튼), `processWork` fieldset만 비활성, `uploading` 중 `beforeunload` 경고. 버튼은 상태별 하나의 주 버튼. 파일 선택 시 `<video>`로 길이·세로 비율 확인(경고만). 제목·설명 카운터는 경고만, `#uploadAndSubmit`만 차단. `<>`는 잡 생성 시 `〈〉`로 치환하고 안내.
11. 제거: `set_thumbnail` 토글(항상 시도, 실패는 경고), `youtube_category_id` 설정(상수 26), 기존 썸네일 모드의 Drive URL 다운로드(`thumb.mode==='new'`일 때만 시도), 별도 `youtube_upload/oauth.py`(service.py로 합침), 수작업 5xx 재시도 루프, IndexedDB v2.
12. 운영: Google 라이브러리는 지연 import + `/status`에 `library_missing`, `run.sh`는 pip 실패 시 경고 출력. 두 서버(8765/8766) 동시 실행 중 업로드 금지 문서화(공유 Store가 busy 잡을 interrupted로 바꾸는 기존 동작).

## 확인한 코드 제약

- `app.py:1930` `allowed` 집합과 `:1940` `fields` 집합은 정확 일치 검사 → `youtube_job_id`는 top-level 선택 키, `fields` 그대로.
- `Code.gs:1482` 필드 수 검사, `:1355` `locked_platform` → `uploadAction`에서 `body.youtubeUrl` 별도 검증·직접 push. `uploadCell`(`:1448`) boolean 미지원 → `boolValue` 분기.
- `hooks/routes.py:204` 이력 목록이 `feature == 'cooking-audio'`만 제외 → `if state.get('feature')`로 일반화(그 위 `:54` before_request는 이미 일반형).
- `hooks/service.py:79` `asset_path(state)`로 자산 경로 조회. `hooks/routes.py:386` `app.extensions['hooks_service']`로 공유 서비스 접근(`cooking_audio/routes.py:17` 패턴). `hooks/store.py:63-73` cleanup은 잡이 참조하는 자산을 보존.
- `access_control.py:37-45` Host·Origin 검사는 비밀번호 있을 때만. `MAX_CONTENT_LENGTH=12MB`, hooks 청크 8MB 통과. OAuth 콜백은 GET.
- `_YT_ID_RE`(`app.py:593`)는 search라 `v=` 포함 문자열도 통과 → 클라이언트 URL을 받지 않는 이유.
- `upload-process.js:166-171` `select()`가 `state=` 대입 후 `uploaded` 예외를 던져 반쪽 상태가 남는 기존 결함 → 예외를 대입 앞으로 이동.

## 파일

새로 만들 파일
- `youtube_upload/__init__.py`
- `youtube_upload/service.py` (~170줄) — 자격증명 파일, OAuth begin/complete, 검증, 잡 생성/실행/재시도, `build_client` seam
- `youtube_upload/routes.py` (~90줄) — Blueprint `/api/youtube/*`; `guarded`/`body`는 `cooking_audio/routes.py:19-34` 복사
- `tests/test_youtube_upload.py` (~180줄)
- `docs/youtube-upload-plan.md` — 이 계획 (승인 후 복사·커밋)

수정할 파일
- `requirements.txt` — `google-api-python-client>=2.100`, `google-auth-oauthlib>=1.2`, `google-auth-httplib2>=0.2`
- `app.py` — submit `youtube_job_id` 처리(~15줄), `install_youtube(app, load_settings, save_settings, settings_lock, sniff_image)`
- `access_control.py` — 로컬 모드 Host 검사 + `/api/youtube/*` Origin 검사
- `hooks/routes.py:204` — feature 필터 일반화 (1줄)
- `sheets/Code.gs` — v15 (4곳, ~6줄)
- `templates/upload_process.html`, `static/upload-process.js`(~120줄), `static/upload-process.css`
- `run.sh` — pip 실패 경고
- `tests/test_upload_process.py`, `tests/test_upload_process.js`, `tests/upload_browser_fixture.py`, `tests/test_upload_browser.js`, `tests/test_access.py`
- `docs/upload-process-setup.md`, `README.md:358-360`(버전 11 문구 갱신), `upload_process.html:50` 문구 제거

## 1. 자격증명·OAuth (`youtube_upload/service.py`)

`~/.youtube-downloader/youtube_credentials.json` (0600, `os.open`+`os.replace`, 모듈 lock):
```json
{"client_id","client_secret","refresh_token","channel_id","channel_title","connected_at"}
```
config.json(비밀 아님): `youtube_audit_passed: false`.

- `SCOPES = ['.../auth/youtube.upload', '.../auth/youtube.readonly']` (readonly: `channels.list(mine=True)`로 채널명 확인 — 브랜드 계정 선택 실수 방지)
- `oauth_available = not APP_PUBLIC_ORIGIN`; `redirect_uri = f'http://127.0.0.1:{PORT}/api/youtube/oauth/callback'` 고정
- `public_status() -> {configured, connected, channel_title, audit_passed, oauth_available, redirect_uri, library_missing, apps_script_version, uploads_today}` (비밀 없음)
- `save_credentials(client_id, client_secret)` / `parse_client_json(text)` (`{"web":{...}}`; `installed`면 안내). 교체 시 refresh_token 폐기
- `begin()`: `Flow.from_client_config(..., redirect_uri)`, `authorization_url(access_type='offline', prompt='consent')`; state를 `session['yt_oauth_state']`에 저장(단일 사용). `OAUTHLIB_RELAX_TOKEN_SCOPE=1`은 모듈 import 시 1회
- `complete(state, code)`: session state 일치 확인 후 `flow.fetch_token(code=code)`; refresh_token 없으면 오류; `channels().list(part='snippet', mine=True)`로 채널명 저장
- `credentials()`: `google.oauth2.credentials.Credentials(...)`; `RefreshError`면 `connected=False` 강등(파일 lock 안에서)
- `disconnect()`: busy 잡 있으면 409; revoke는 POST body; refresh_token·채널 제거(client_id/secret 유지)
- Google 라이브러리는 함수 내부에서 import(`app.py:2135` anthropic 패턴). ImportError → `library_missing=True`

라우트: `GET /status`, `POST /credentials`, `POST /settings` (`audit_passed`만), `POST /oauth/start` → `{url}` (JS가 `await persist()` 후 `location.assign`), `GET /oauth/callback` → 302 `/upload-process?youtube=connected|error&code=<짧은 코드>`, `POST /disconnect`.

## 2. 업로드 잡 (`youtube_upload/service.py`)

공유 hooks `Service`를 감싸는 `Service(shared)` (`cooking_audio/service.py:25-27` 패턴). 잡은 `store.write('jobs', id, {feature:'youtube-upload', ...})`.

- `validate_metadata(title, description)`: title 1–100자, 설명 ≤5000 UTF-8 바이트, `\r\n→\n`, `<`/`>` → `〈`/`〉` 치환(치환 시 warnings에 안내). `CATEGORY_ID='26'` 상수, `privacyStatus='private'`, `selfDeclaredMadeForKids=False`
- `create_job(asset_id, item_id, connection, title, description, thumbnail bytes|None, force=False)`:
  - 연결 안 됨 → 409 `youtube_not_connected`; `audit_passed`이고 `ping` 버전 <15 → 409 `upgrade_required`(ping 결과 5분 캐시)
  - 같은 `item_id`+`connection`으로 busy 잡 → 409 + 그 잡의 public state 반환(클라이언트 재접속); done 잡 → `force` 없으면 409 + state, 있으면 새 잡
  - 자산 검증: `shared.asset_path(state)`, `info`(ffprobe 결과)에서 duration/해상도 읽어 state에 기록
  - 썸네일: `f.read(THUMB_MAX_BYTES+1)`, `sniff_image`, `jobs/<id>/thumbnail.jpg`
- 상태: `{id, feature, item_id, connection, asset_id, title, description, name, size, busy, status: queued|uploading|finalizing|thumbnail|done|error|interrupted, percent, message, video_id, url, error_code, warnings[], test_mode, created, updated}`
- `start(ident)`: hooks `Service.start` 재사용
- `run(ident)`:
  1. `build_client(creds)` = `build('youtube','v3', credentials=creds, cache_discovery=False, static_discovery=True)`; 스레드마다 새 인스턴스
  2. `MediaFileUpload(path, mimetype, chunksize=8MB, resumable=True)`; `videos().insert(part='snippet,status', body=...)`
  3. `status, response = request.next_chunk(num_retries=5)` 루프, `percent` 갱신; 마지막 조각 전 `status='finalizing'`
  4. `HttpError` 4xx → 즉시 실패, `error_code`는 `resp.status`+`error_details[0].reason`만 저장(원문은 stderr 로그), 한국어 매핑: `quotaExceeded`/`uploadLimitExceeded`→"오늘 API 한도 초과 · 한국 시간 17시 이후 재시도 또는 영상 없이 제출", `invalid_grant`→"Google 연결을 다시 해주세요", `forbidden`
  5. 성공: `video_id`, `url`; 썸네일 있으면 `thumbnails().set` → 성공 시 warnings에 "썸네일 설정을 요청했습니다. 반영은 Studio에서 확인하세요", 실패(`forbidden` 등)는 경고; 자산 `video` 파일 삭제; `done`
  6. 실패: `error`, 자산 유지
- `retry(ident)`: error/interrupted + 자산 존재 시 재시작. 직전 `percent>=99` 또는 `finalizing`이면 warnings에 "마무리 단계에서 중단됨. Studio에서 영상이 이미 있으면 재시도하지 말고 '기록'에서 확인" 추가
- `list_jobs(connection=None, item_id=None)`: `jobs/*/state.json` glob, `feature=='youtube-upload'`, 최신 50개

라우트: `POST /jobs`(multipart payload + `thumbnail`), `GET /jobs?item_id=&connection=`, `GET /jobs/<id>`, `POST /jobs/<id>/retry`. 영상 전송은 **기존** `POST /api/hooks/uploads` → `PUT .../chunks/<i>` → `POST .../complete`(`asset_id`) 그대로 사용.

## 3. `app.py` submit (`:1923-1967`)

```python
allowed = {...기존 6개}; optional = {'youtube_job_id'}
if not allowed <= set(body) <= allowed | optional: 400
extra = {}
if 'youtube_job_id' in body:
    job = youtube_service.job_for_submit(body['youtube_job_id'])   # valid_id, feature, status=='done', item_id/connection 일치, video_id
    if not job: 409 code 'youtube_job_invalid'
    extra['youtubeUrl'] = f"https://youtu.be/{job['video_id']}"
result = sheet_call('upload_submit', ..., **image, **extra)
if extra and _s(result['cells'].get('youtubeUrl')) != extra['youtubeUrl']:
    out['warnings'].append(f"Apps Script 15 미만 버전이라 유튜브 링크({extra['youtubeUrl']})가 기록되지 않았습니다. 현황판에서 직접 입력해 주세요.")
```

## 4. `access_control.py`

- 비밀번호 없을 때: `HOST != '0.0.0.0'`이면 `request.host ∉ {f'{HOST}:{PORT}', f'127.0.0.1:{PORT}', f'localhost:{PORT}'}` → 400
- 비밀번호 없을 때 `request.path.startswith('/api/youtube/')`이고 non-GET이면 Origin == `request.host_url` 검사(403)
- `tests/test_access.py`에 두 케이스 추가; 기존 테스트 클라이언트는 기본 Host `localhost`라 통과 확인

## 5. Code.gs v15 (`uploadAction`, 4곳)

- `:41` `UPLOAD_VERSION = 15`
- `:1450` `uploadCell`: `typeof value === 'boolean' ? {boolValue:value} :` 분기
- `:1484` 뒤: `if (body.youtubeUrl !== undefined && !/^https:\/\/youtu\.be\/[A-Za-z0-9_-]{11}$/.test(body.youtubeUrl)) return bad_fields;` 및 `if (body.youtubeUrl && before.youtubeUrl && before.youtubeUrl !== body.youtubeUrl) return conflict;`
- `:1500` digest: `...(body.youtubeUrl ? [body.youtubeUrl] : [])` (구 payload 해시 불변 → 배포 전 `commit_unknown` 재시도 호환)
- `:1543` 뒤: `if (body.youtubeUrl) writes.push({key:'youtubeOn',value:true},{key:'youtubeUrl',value:body.youtubeUrl});`
- `uploadReply`·`UPLOAD_COMPARE` 변경 없음

## 6. 클라이언트

템플릿 (`upload_process.html`):
- `#conflictBox` 아래 `<details id="youtubeBox" class="card">`: summary "유튜브 채널: 미연결 / <채널명> · 테스트 모드"; client JSON textarea + 저장; `button#youtubeConnect`(공개 모드면 비활성 + "로컬 127.0.0.1에서 한 번만 연결"); 해제; 리디렉션 URI 복사; **감사 토글** "YouTube API 감사 승인 완료 — 승인 전에는 테스트 업로드만 하며 현황판에 제출하지 않습니다"; "오늘 API 업로드 N건"; **유튜브 업로드 기록** 목록(제목·링크·상태·시각, `GET /jobs`)
- step 1 `#videoTitle` 아래 "유튜브 제목 N/100자" 경고만; step 2 `#youtubeOut` 아래 "유튜브 설명 N/5000바이트" 경고만(`confirmRecipe` 동작 불변)
- step 4 `#youtubePanel`: 연결 상태 + 설정 열기 링크, `input#videoFile accept="video/mp4,video/quicktime"`, 선택 시 `<video>` 미리보기·길이·해상도(>180초 또는 세로 아님이면 "쇼츠로 인식되지 않을 수 있습니다" 경고), 테스트 모드 빨간 배너, `progress#youtubeProgress` + `#youtubeStatus` + `button#youtubeCancel`, `#youtubeDone`(링크), `#youtubeRetry`
- 버튼: 연결+파일 선택 → `#uploadAndSubmit` "유튜브에 비공개 업로드 후 현황판에 제출"(테스트 모드면 "테스트 업로드 (현황판 제출 없음)") + `#submitProcess` secondary "영상 없이 현황판에만 제출"; 미연결 → `#submitProcess`만 기존 라벨 "현황판에 제출" + 힌트; 기존 잡 `done` → `#submitProcess` 하나 "현황판에 제출 (유튜브 링크 포함)"·파일 입력 숨김
- `upload_process.html:50` "실제 유튜브 게시 기능은 포함하지 않습니다" 제거

`UploadProcessData` export: `youtubeMeta(title, desc) -> {ok, errors, titleLen, descBytes}`, `youtubeUrl(id)`

런타임 상태(메모리만, 초안에 저장하지 않음): `youtubeJob = null | public state`. 파일 `File` 객체와 `asset_id`는 저장하지 않는다.

흐름:
1. 로드: `GET /api/youtube/status` → 패널·버튼; 쿼리 `youtube=connected|error` 처리 후 `replaceState`, connected면 `#youtubeBox` 열기
2. `select()` 성공 후 `GET /jobs?item_id=&connection=` → busy면 폴링 재개, done이면 `#youtubeDone`·버튼 전환, error면 `#youtubeRetry`. 404/없음이면 초기 상태. `uploaded` 항목에 done 잡이 있으면 message에 링크 표시(항목은 목록에 없음 → 기록 목록에서 확인)
3. `#uploadAndSubmit`: 가드 → `youtubeMeta` → confirm(`"${name} (${MB}) 을 유튜브에 비공개로 업로드합니다. 완료되면 현황판에 저장하고 ✅ 업로드 완료로 변경합니다. 몇 분 걸릴 수 있습니다."`, 테스트 모드면 "현황판에는 제출하지 않습니다") → `busy=true; uploading=true; processWork.disabled=true` → `uploadVideo(file)`(`hooks.js:127-141` 이식, `/api/hooks/uploads`, AbortController) → `POST /api/youtube/jobs`(FormData: payload + `state.thumb.blob`) → `pollJob()` 1초, 네트워크 오류는 2→10초 백오프로 "서버 응답 대기 중" → done: 테스트 모드면 `#youtubeDone`만, 아니면 `submitSheet(job.id)`; error/interrupted: `error_code`별 안내 + `#youtubeRetry`
4. `submitSheet(jobId)` = 기존 `#submitProcess` 본문 추출, payload에 `youtube_job_id` 동반. `sendPending`/`checkSubmission` 변경 없음
5. 유튜브 성공 + 시트 실패/충돌/`already_uploaded`: message에 링크 + "유튜브 업로드는 완료. 현황판 제출만 다시 시도"; `restartDraft` confirm 문구에 "유튜브 업로드 결과는 유지됩니다" 추가; 재시작 뒤 2단계에서 서버 조회로 `done` 복원
6. `finish(data)`: `completedInfo`에 유튜브 링크
7. `beforeunload`: `unsaved||persistFailed||uploading`
8. `select()` 기존 결함 수정: `uploaded` 예외를 `state=` 대입 앞으로

## 7. 테스트

`tests/test_youtube_upload.py` — `HOOKS_DATA_DIR` 임시 디렉터리(`test_hooks.py:72`), 자격증명 파일 경로 patch, `youtube_upload.service.build_client` patch → `FakeYouTube`(`next_chunk` 순차, 4xx 옵션, `thumbnails().set` 예외 옵션, `channels().list`):
- 메타: 101자, 5001바이트 → 400; `<>` 치환 + warnings
- 성공: 자산 video 삭제·url·done; 4xx: 자산 유지 + retry 성공; `finalizing` 후 interrupted → retry warnings
- 썸네일 `forbidden` → 경고 + done
- 같은 item_id busy/done 409 + state 반환, `force` 통과; Store 재생성 busy→interrupted
- `audit_passed=true` + ping 14 → 409 `upgrade_required`; 미연결 409
- OAuth: credentials 저장 후 `/status` secret 미노출·파일 0600; `/oauth/start` POST → url에 client_id·state·`access_type=offline`, session state 저장; 잘못된 state → error redirect; 정상 콜백(`Flow` patch) → connected·channel_title; busy 중 disconnect 409; disconnect 후 configured/connected
- 공개 모드(`APP_PUBLIC_ORIGIN`) `oauth_available=False`
- 접근 제어(`test_hooks.py:113` 패턴): `POST /jobs` Origin 없음 403(로컬 모드 포함), 콜백 비로그인 401

`tests/test_upload_process.py` — `youtube_job_id`(done 잡) → Apps Script 인자 `youtubeUrl`, `fields` 불변; 잡 없음/미완료/다른 item_id → 409 네트워크 미호출; 결과 cells에 없으면 링크 포함 경고
`tests/test_access.py` — 로컬 모드 Host 불일치 400, `/api/youtube/*` Origin 검사
`tests/test_upload_process.js` — `youtubeMeta` 경계, `youtubeUrl`
`hooks/routes.py:204` 변경으로 `test_hooks.py` 이력 테스트 통과 확인

Playwright — fixture(`upload_browser_fixture.py`): `HOOKS_DATA_DIR`, `build_client` patch(control `inserts`, `yt_fail`), `public_status` 토글, `call()`이 `youtubeUrl` 기록, `hooks_fixture.make_video`로 실제 mp4 생성. 시나리오 2개 + 단언 1개: (a) 파일 선택 → `#uploadAndSubmit` → `#completed`, records youtubeUrl, `inserts===1`; (b) `lost` 조합: 유튜브 done → 시트 유실 → 리로드 → `checkSubmission` → `#completed`, `inserts===1`; (c) 미연결 기본 상태에서 `#uploadAndSubmit` 미표시 + 기존 흐름 무변경

## 8. 문서 (`docs/upload-process-setup.md` v15 절, `README.md`)

0. **먼저**: Google Cloud 프로젝트 → YouTube Data API v3 사용 → [API 규정 준수 감사](https://support.google.com/youtube/contact/yt_api_form) 신청(단일 사용자 도구 설명·데모 영상·개인정보처리방침 URL 요구 가능, 수 주). 승인 전 업로드는 비공개 잠김·이의신청 불가
1. Code.gs 교체 → `UPLOAD_VERSION` 15 → 새 버전 배포 → `/exec` version 15 확인
2. `run.sh` 재실행(의존성 설치; 실패 시 경고 확인)
3. OAuth 동의 화면(Workspace 채널 → 내부 / 개인 → 외부 + 프로덕션, "확인되지 않은 앱" 화면은 고급 → 이동) → 범위 2개 → **웹 애플리케이션 클라이언트** → 리디렉션 URI `http://127.0.0.1:8765/api/youtube/oauth/callback` 하나 → JSON 다운로드
4. 로컬 앱 설정 패널에 JSON 붙이기 → Google 연결(브랜드 계정이면 채널 선택) → 채널명 확인. 자격증명은 `~/.youtube-downloader/youtube_credentials.json`(0600)
5. 테스트 모드로 짧은 영상 업로드 → Studio에서 영상·썸네일 확인 → 감사 승인 후 토글 켜기 → 실제 항목 업로드 → 시트 ▶️ 체크·링크 확인
6. 문제 해결: `invalid_grant`→재연결, 한도 초과, 99% 이후 중단 시 Studio 확인, 썸네일 미반영 시 Studio에서 직접, 로컬·공개 서버 동시 실행 중 업로드 금지
7. `docs/upload-process-setup.md:3`·`upload_process.html:50` "실제 유튜브 게시 기능은 아닙니다" 제거, `README.md:358-360` 버전 문구 갱신

## 9. 2차 (감사 통과 후)

공개/일부공개/예약 선택 + `publishAt` 검증, 카테고리 선택, 청크 업로드 이어올리기

## 10. 구현 순서

1. `access_control.py` Host/Origin + 테스트
2. requirements + `youtube_upload/` + `hooks/routes.py:204` + app.py install, Python 테스트
3. app.py submit `youtube_job_id` + 테스트
4. Code.gs v15 (오프라인 검토, 배포는 사용자)
5. 템플릿/JS/CSS + Node 테스트
6. fixture·Playwright
7. 문서, `run.sh`, `docs/youtube-upload-plan.md` 복사 후 커밋(한국어 메시지, Co-Authored-By 없음)

## 11. 검증

```sh
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v
node tests/test_upload_process.js
.venv/bin/python tests/upload_browser_fixture.py   # 터미널 1
node tests/test_upload_browser.js                  # 터미널 2
```
수동(사용자): `run.sh` → JSON 저장 → Google 연결 → 채널명 → 테스트 모드 업로드 → Studio 확인 → (감사 후) 토글 → 실제 업로드 → 시트 반영 → `run-public.sh`에서 연결 버튼 비활성·업로드는 동작 확인.

## 리스크

- 감사 기간 불확실 → 테스트 모드로 파이프라인은 먼저 검증
- YouTube 업로드는 멱등이 아님 → 99% 이후 중단 시 사용자 확인에 의존(1차)
- 한글 설명 5000바이트 → 2단계 카운터 + 서버 검증
- Python 3.14 venv에 Google 라이브러리 설치 미검증 → 지연 import로 앱 전체 장애 방지
