# 쇼츠 현황판 전체 필드 편집 기능

## Context

`/shorts` 현황판은 구글 시트 '현황판' 탭을 CSV로 읽어 보여주는 뷰어이고, 쓰기는 썸네일 등록·연결 설정만 가능하다. 팀원이 상태·제목·링크 등을 고치려면 매번 시트로 이동해야 한다. 이번 작업으로 현황판 페이지에서 **모든 필드를 편집하고, 행을 추가·삭제**할 수 있게 한다.

사용자 결정 사항:
- UI는 **행 펼침(▶) 편집 폼** 방식. 폼 전체를 한 번의 저장으로 반영.
- 자동 채움 필드(원본 제목·원본 채널·참고 채널)도 직접 수정 가능. 단 원본 링크/참고 링크가 바뀌면 시트 트리거처럼 자동 재채움.
- 플랫폼 체크·링크는 상태가 '업로드 완료'일 때만 편집 가능(시트와 같은 규칙).
- 행 추가('＋ 새 항목', 맨 위 3행에 촬영 후보로 생성)와 삭제(확인 창) 모두 웹에서 가능.

쓰기 경로는 기존 썸네일/후보 등록과 같이 **Flask → Apps Script 웹 앱(`doPost`) → 시트**를 사용한다. 시트 CSV 내보내기가 몇 초 늦으므로 기존 `apply_recent_thumbs` 패턴을 일반화한 "최근 편집 오버레이"로 저장 직후 화면을 맞춘다.

## 설계상 주의점 (탐색으로 확인됨)

1. **빈 행은 웹에 안 보인다.** `parse_sheet_items`(app.py:665~667)는 요리 제목·원본 링크·영상 제목이 모두 비면 행을 건너뛴다. 새 항목(상태+날짜만 있음)이 사라지지 않도록 `등록일`이 있으면 유지하도록 조건 확장.
2. **3행 삭제 시 제목 글자수 수식이 사라진다.** `titleLen` ARRAYFORMULA는 R3에만 있다(Code.gs:370~376). insert/delete 후 수식을 다시 심는 `ensureLenFormulas(sheet)`를 추출해 호출.
3. **수식 주입.** `setValue`에 `=`로 시작하는 문자열을 넣으면 수식이 된다. Flask와 Code.gs 양쪽에서 선행 `=` 제거.
4. **`ss.toast()`가 doPost 컨텍스트에서 실패할 수 있다.** `fillSource`(Code.gs:611)와 `revertPlatformCell`(Code.gs:574)의 toast를 try/catch `safeToast()`로 감싼다.
5. **행 식별.** `locateRow`는 srcUrl→dish 순으로 확인하고, 둘 다 없으면 행 번호를 그냥 믿는다. 웹에서 만든 빈 행은 `createdAt`(분 단위 문자열)을 추가 키로 확인. 같은 분에 만든 빈 행 둘은 구분 불가 → `row_mismatch`로 거절하고 새로고침 안내(README에 기록).
6. **10초 폴링이 편집 폼을 지운다.** `render()`가 `#rows.innerHTML`을 통째로 바꾼다(shorts.html:429). 열려 있는 `tr.detail`은 DOM에 남기고 나머지 행만 교체하는 `swapRows()`로 대체.

---

## 1. Apps Script — `sheets/Code.gs`

- `UPLOAD_VERSION = 8` → `9` (L40). 상단 주석에 `board_*` 언급.
- 상수: `BOARD_EDITABLE` = COLUMNS 중 `lenOf`·`date`·`thumb`·`thumbUrl` 제외한 key 집합.
- 헬퍼 추가:
  - `boardCellText(v)`: Date → `yyyy-MM-dd HH:mm`(L351 셀 서식과 동일), boolean 그대로, 나머지 `String`.
  - `boardReadRow(sheet,row)` → `{cells}` (thumb·titleLen 제외 전 컬럼, COL key 기준).
  - `ensureLenFormulas(sheet)`: L370~376 로직 추출. `nextCandidateRow`(L781) 삽입 후와 모든 `deleteRow` 후 호출.
  - `safeToast(msg)`.
  - `boardValidateFields(fields, finalStatusLabel, currentRowValues)` → 에러 문자열 또는 `writes[{col,value}]`. `status`는 `boardLabelOf`(L758)로 정규화 후 `STATUSES[].label`에 없으면 `bad_status`. 체크박스는 boolean, 텍스트는 선행 `=` 제거. 현재 셀과 같은 값은 스킵. 플랫폼 컬럼에 값을 넣는데 최종 상태가 `UPLOADED`가 아니면 `locked_platform` (비우는 것은 허용, `revertPlatformCell`과 동일).
- `locateRow(sheet,row,srcUrl,dish,createdAt)` (L1191): 5번째 인자 추가. srcUrl·dish가 모두 없고 createdAt이 있으면 등록일 컬럼으로 확인/검색(유일 매치만). 키 검색 결과가 여럿이면 createdAt으로 필터.
- `doPost` 디스패치(L688)에 `if (String(body.action||'').startsWith('board_')) return boardAction(body);`
- `boardAction(body)`: `candidateAction`(L791~821) 구조 복제 — `sheetId` 검사(`wrong_sheet`), `no_sheet`, `LockService.tryLock(20000)`→`busy`, try/catch/finally.

| action | 요청 | 성공 응답 |
|---|---|---|
| `board_update` | `row, srcUrl, dish, createdAt, fields:{COL key→값}` (변경된 것만; status는 이모지 없는 라벨) | `{ok,row,changed:[keys],cells}` |
| `board_add` | `fields` (선택) | `{ok,row,cells}` |
| `board_delete` | `row, srcUrl, dish, createdAt` | `{ok,row,cells}` |

- `board_update` 흐름: `locateRow`(0→`row_mismatch`) → 현재 행 읽기 → 검증(쓸 것 없으면 `bad_fields`) → `beforeIds=boardRowVideoIds` → setValue → `srcUrl` 바뀌면 `fillSource(sheet,row,true)`, `refUrls` 바뀌면 `fillRefs(sheet,row,true)` → `touchRow` → status/refUrls/srcUrl 중 하나라도 바뀌면 `refSyncBoardVideos(ss, before∪after)` (handleEdit L506~514 미러) → `boardReadRow` 반환.
- `board_add`: 필드 검증 먼저 → `nextCandidateRow` → `ensureLenFormulas` → 상태 `STATUSES[0].label`, updatedAt/createdAt=now, `setRowHeight`, `ensureBoardFilter`(boardAddCandidate L766~778 참고) → writes 적용 → fill*/refSync → 반환.
- `board_delete`: `locateRow` → cells·videoIds 읽기 → `deleteRow` → `ensureLenFormulas` → `refSyncBoardVideos` → 반환. (드라이브 썸네일 파일은 남김.)
- 에러 코드 추가: `row_mismatch, bad_row, bad_fields, bad_status, locked_platform`.

## 2. Flask — `app.py`

- 상수(L558~576 근처): `SHEET_FIRST_DATA_ROW = 3`, `SCRIPT_FIELDS = {py_key: COL key}` 명시 매핑(`src_url→srcUrl`, `naver_clip_on→naverClipOn`, `updated→updatedAt` 등), `BOARD_TEXT_LIMITS`(dish 200, url 2000, src_title 500, channel 200, ref_urls 5000, ref_channels 2000, title 500, desc/pinned 20000, memo 5000), `BOARD_BOOL_FIELDS = {f"{k}_on"}`.
- 파서 리팩터(L647~690): 루프 본문을 `_item_from_cells(sheet_row, cell)`로 추출. 스킵 조건에 `created` 추가. `item_from_script(row, cells)`는 Apps Script `cells`를 같은 함수로 item으로 변환(boolean은 `_s(True).upper()=="TRUE"`, 상태는 `_clean_header`, 날짜는 `_iso` 그대로 동작).
- `_board_patch(raw) -> (fields|None, error|None)`: 비어있지 않은 dict, 알 수 없는 키 400, `status`는 `STATUSES` 키 → `STATUSES[key]` 라벨로 변환, bool 파싱, `ref_urls`/`ref_channels`는 list 또는 문자열 → 줄 정리 후 `\n` join, 텍스트는 `_s` + `lstrip("=")` + **초과 시 400**(잘라내지 않음). 결과는 `SCRIPT_FIELDS`로 키 변환.
- `_sheet_stamp(iso)`: `_iso`의 역변환(`T`→공백).
- `_SHEET_ERRORS`(L1420)에 `row_mismatch(409), bad_row(400), bad_fields(400), bad_status(400), locked_platform(409)` 추가. 썸네일 엔드포인트 인라인 맵(L1582~1587)은 그대로 둠.
- 라우트(L1489 뒤):
  - `PUT /api/shorts/items/<int:row>` body `{src_url, dish, created_at, fields}` → `_sheet_call("board_update", ...)` → `item_from_script` → `remember_recent_edit("update", item)` → `_sheet_cache_invalidate()` → `{ok, item}`.
  - `POST /api/shorts/items` body `{fields?}` → `board_add` → `remember_recent_edit("insert", item)` → `{ok, item}`.
  - `DELETE /api/shorts/items/<int:row>` body `{src_url, dish, created_at}` → `board_delete` → `remember_recent_edit("delete", item)` → `{ok, row}`.
  - row < 3 → 400. 연결 미설정은 `sheet_call`이 400 처리.
- 최근 편집 오버레이(`_recent_thumbs` L757~786 옆에 추가):
  - `RECENT_EDIT_TTL = 180`, `_recent_edits: list[{"kind","row","item","at"}]`.
  - `remember_recent_edit(kind, item)`: `sheet_lock` 하에서, insert/delete면 `_recent_edits`·`_recent_thumbs` 전부 초기화(행 번호 이동) 후 append.
  - `_identity(it)` = (created_at, dish_title, source.url, video.title); `_edit_sig(it)` = identity + 나머지 편집 필드 + updated_at.
  - `apply_recent_edits(items)`: TTL 만료 정리 → 순서대로 적용. `update`: 같은 row·identity면 시그니처 일치 시 op 제거, 아니면 item 교체. `insert`: identity가 이미 있으면 제거, 없으면 `row>=op.row` +1 시프트(id 재계산) 후 삽입. `delete`: 같은 row·identity가 아직 있으면 제거하고 `row>op.row` −1 시프트, 없으면 op 제거.
  - `GET /api/shorts`(L1406): `apply_recent_edits(apply_recent_thumbs(items))`.

## 3. 프론트엔드 — `templates/shorts.html`

- 문구/마크업: L219 버튼 "시트 열기 ↗" + `#addBtn` "＋ 새 항목" 추가; L191 업로드 박스 제목 "시트 쓰기 연결 (편집·썸네일)"; L351 상태 문구; L251 footer를 "이 화면에서 항목을 추가·수정·삭제할 수 있습니다…"로 교체; L460 행 액션에 "시트 ↗" 링크 + `data-act="delete"` 삭제 버튼.
- CSS: `.form-actions`, `.form-meta`, `tr.busy td{opacity:.5}`, `.plat-row` 그리드, `.field input`. 기존 `.plat-row`/`fieldset.locked`/`.lock-hint`(L104~109), `.counter/.over`(L140) 재사용.
- 상세 행(L463~472)을 편집 폼으로: `field()`를 `fieldEl(label,name,value,{kind,max,full,mono,copy})`로 일반화, 모든 컨트롤에 `data-f="<flat key>"`.
  1. 상태 `<select>`(STATUS_ORDER) | 요리 제목
  2. 원본 링크 | 원본 제목·원본 채널 (자동 재채움 힌트)
  3. 참고 쇼츠 링크(textarea, 줄당 1개) | 참고 채널(textarea)
  4. full: `<fieldset class="plat-fs">` 4개 `.plat-row`(체크박스 `{k}_on`, 아이콘, URL `{k}_url`). 상태 select ≠ uploaded면 `locked` + disabled.
  5. 영상 제목(카운터 100, 복사) | 메모
  6. full 설명(카운터 5000, 복사) 7. full 고정 댓글(mono, 복사)
  8. `.form-actions`: 좌 "등록 … · 수정 …" + "시트에서 보기 ↗"(rowUrl), 우 취소·저장(dirty && !saving일 때만 활성). `!sheet.upload_configured`면 저장 비활성 + 힌트.
- 상태: `edit = null | {id,item,snapshot,dirty,saving}`, `busyRows = new Set()`. `flatOf(it)`, `formValues(tr)`, `diffFlat(a,b)`. `api()`에 `err.code/err.status` 부착.
- `openEdit(it)`/`closeEdit(force)`(dirty면 confirm). `#rows`에 `input`/`change` 위임: dirty 재계산, 카운터 갱신, status 변경 시 플랫폼 잠금 토글. `beforeunload` 가드, Esc 닫기, Cmd/Ctrl+Enter 저장.
- `swapRows(html)`: 열려 있는 `tr.detail[data-id=edit.id]`가 있으면 그 노드는 유지하고 나머지만 교체(요약 행은 바로 앞에 삽입). 편집 중 항목이 필터로 빠졌으면 `edit.item`으로 요약 행을 합성해 맨 위에. 폴링은 멈추지 않음.
- 저장: `diffFlat(snapshot, formValues())` → `PUT /api/shorts/items/{row}` with `{src_url, dish, created_at(현재 item 기준), fields}`. 성공: items 교체, 서버 반환 item으로 폼 재오픈(자동 채움 결과 표시), toast, `boardBus.postMessage('changed')` + `localStorage['shorts-board-change']`(references.html L107 패턴), `setTimeout(load(true),3000)`. 실패: toast; `row_mismatch`면 `load(true)`.
- 삭제: `confirm()` → `busyRows.add(row)` → DELETE → 성공 시 items에서 제거·row 시프트·edit 정리·toast·broadcast·지연 reload. finally busy 해제.
- 새 항목: `sheet.configured && upload_configured`일 때만 활성. dirty면 toast로 안내 후 중단. POST → items 시프트 후 unshift, 필터 초기화(saveFilters), `openEdit` + 요리 제목 포커스, toast, broadcast, 지연 reload.

## 4. 테스트 — `tests/test_shorts.py`

기존 `SHEET`/`UPLOADER` 픽스처(L50~51)와 `patch.object(appmod, "get_sheet_setting"/"get_upload_setting"/"apps_script_post")` 패턴 사용.

- `ParseTest`: `test_blank_registered_row_kept`(등록일만 있는 행 유지, 완전 빈 행은 스킵), `test_item_from_script_matches_csv_shape`.
- `BoardItemApiTest`: `test_update_sends_fields_and_returns_item`(payload action/row/createdAt/fields 키 변환, 응답 item, 캐시 무효화, `_recent_edits` 1건), `test_update_validation`(row 2, fields 없음, 알 수 없는 키, 잘못된 상태, 201자 dish → 400, post 미호출), `test_update_strips_formula_prefix`, `test_update_error_codes`(`row_mismatch 409, locked_platform 409, bad_status 400, busy 503, unknown_action 409, weird 502`), `test_add_clears_overlays`, `test_delete`, `test_requires_connections`.
- `RecentEditTest`(`RecentThumbTest` L223~251 스타일): update 수렴 전 오버레이/수렴 후 제거, identity 불일치 스킵, insert 시프트/수렴, delete 숨김/수렴, TTL 만료, 구조 변경 시 이전 op 초기화.

## 5. 문서

- `README.md` L141~169: "읽기 전용 뷰어" 문구 제거, 요리 제목 클릭 → 편집 폼, 새 항목·삭제 설명, 플랫폼 잠금 규칙, "시트 쓰기 연결"이 편집·추가·삭제·썸네일에 필요함.
- `sheets/README.md`: L110 제목에 "현황판 편집" 추가, 액션 목록에 `board_update/board_add/board_delete`, L206 `"version":8` → `9`, 문제 해결에 "같은 분에 만든 빈 행 둘은 구분 불가" 항목.

## 6. 검증

1. `.venv/bin/python -m unittest -v` 전체 통과.
2. Apps Script: `Code.gs` 붙이기 → `setupSheets` 1회 → 배포 관리 › 새 버전 → 웹 앱 URL에서 `"version":9` 확인. `/api/shorts/candidates` 정상 동작 확인.
3. 실제 시트에서 수동 확인:
   - 새 항목 → 3행 폼 열림 → 요리 제목 + 유튜브 원본 링크 입력 → 저장 → 원본 제목/채널 자동 채움, 시트 3행 반영, R열 글자수 유지. 30~60초 대기해도 행이 사라지지 않음.
   - 상태를 업로드 완료로 → 플랫폼 입력 활성 → 유튜브 체크+URL 저장 → 시트 TRUE/링크. 상태 되돌리면 비활성.
   - `curl -X PUT` 로 비업로드 행에 `youtube_on:true` → 409 `locked_platform`.
   - 삭제 → 확인 → 웹·시트에서 사라짐, R3 수식 유지.
   - 두 탭에서 한쪽 저장 → 다른 쪽 즉시 갱신. 폼 입력 중 10초 폴링에도 커서·텍스트 유지.
   - 시트에서 위에 행을 수동 삽입한 뒤 웹에서 저장 → `locateRow` 키 검색으로 성공. 웹에서 만든 빈 행은 409 → 새로고침 안내.
   - 썸네일 업로드가 기존처럼 동작하고 오버레이가 유지됨.

## 수정 파일

- `sheets/Code.gs`
- `app.py`
- `templates/shorts.html`
- `tests/test_shorts.py`
- `README.md`, `sheets/README.md`
