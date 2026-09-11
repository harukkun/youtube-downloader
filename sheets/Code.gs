/**
 * 쇼츠 현황판 — Google Sheets + Apps Script
 *
 * 사용법 (한 번만):
 *   1) 새 스프레드시트 → 확장 프로그램 › Apps Script → 이 파일 내용을 Code.gs 에 붙여넣고 저장
 *   2) 편집기 상단 함수 선택에서 setupSheets 를 고르고 ▶ 실행 → 권한 승인
 *   3) 시트로 돌아오면 '현황판'·'요약' 시트와 '쇼츠 현황판' 메뉴가 생긴다
 *
 * 자동화:
 *   - 원본 링크 열에 유튜브 링크를 붙이면 원본 제목·채널이 채워진다
 *   - 참고 쇼츠 링크 열(여러 개는 줄바꿈)을 채우면 참고 채널이 줄마다 채워진다
 *   - 상태가 '✅ 업로드 완료'가 아닌 행에서는 플랫폼 체크·링크 입력이 되돌려진다
 *   - 행을 고치면 수정일, 요리 제목을 처음 넣으면 등록일이 찍힌다
 *
 * 썸네일 업로드 (웹 앱):
 *   - 썸네일 열은 원본 영상이 아니라 내가 재가공한 쇼츠의 썸네일 자리다.
 *   - 로컬 앱(/shorts, /helper 썸네일 만들기)이 이미지를 보내면 doPost 가 구글 드라이브 폴더에 저장하고
 *     해당 행의 썸네일 열에 =IMAGE(), 숨김 열 '썸네일 링크'에 URL 을 적는다.
 *   - 배포 › 새 배포 › 웹 앱 (실행 계정: 나, 액세스: 모든 사용자). 메뉴 🔑 에서 URL·토큰을 확인해 로컬 앱에 입력.
 *   - 코드를 고친 뒤에는 배포 관리 › 새 버전 을 만들어야 웹 앱에 반영된다.
 *
 * setupSheets 는 다시 실행해도 안전하다. 이전 버전 배치(헤더가 1행, 설명 글자수 열, 이모지 없는 상태값,
 * 원본 영상 썸네일 수식)는 자동으로 새 배치로 옮긴다. 입력한 데이터는 지우지 않는다.
 *
 * Apps Script 는 자바스크립트(V8)다. UrlFetchApp/SpreadsheetApp/DriveApp 등은 구글 제공 객체.
 */

const SHEET_NAME = '현황판';
const SUMMARY_NAME = '요약';
const GROUP_ROW = 1;        // 열 그룹 띠 (📋 기본 · 🎬 원본 영상 …)
const HEADER_ROW = 2;       // 열 이름
const FIRST_DATA_ROW = 3;
const MIN_ROWS = 1000;
const ROW_HEIGHT = 64;      // 썸네일이 보이는 데이터 행 높이

// 썸네일 업로드 (웹 앱). 토큰·폴더 ID 는 스크립트 속성에 보관한다.
const PROP_TOKEN = 'UPLOAD_TOKEN';
const PROP_FOLDER = 'THUMB_FOLDER_ID';
const THUMB_FOLDER_NAME = '쇼츠 현황판 썸네일';
const UPLOAD_VERSION = 8;
const thumbUrlFor = (id) => `https://lh3.googleusercontent.com/d/${id}`;   // IMAGE() 와 <img> 모두에서 열리는 형식
const SOURCE_THUMB_RE = /i\.ytimg\.com|img\.youtube\.com/;                 // 예전 버전이 넣던 원본 영상 썸네일

const stripEmoji = (s) => String(s || '').replace(/^[^\p{L}\p{N}]+/u, '').trim();   // '📌 상태' → '상태'
// legacy: 이전 버전에서 쓰던 라벨(이모지 제외). setupSheets 가 데이터 행의 옛 라벨을 새 라벨로 바꾼다.
const STATUSES = [
  { label: '⭐ 촬영 후보',   legacy: ['후보', '제작 전'],      bg: '#f3e8ff', fg: '#7e22ce' },   // '제작 전' 단계는 없어졌다 → 촬영 후보로 합친다
  { label: '🎬 촬영 중',     legacy: ['제작 중'],             bg: '#dbeafe', fg: '#1d4ed8' },
  { label: '✂️ 편집 중',     legacy: [],                      bg: '#e0f2fe', fg: '#0369a1' },
  { label: '⏳ 업로드 대기', legacy: ['제작 완료·업로드 대기'], bg: '#fef3c7', fg: '#b45309' },
  { label: '✅ 업로드 완료', legacy: [],                      bg: '#dcfce7', fg: '#15803d' },
];
const UPLOADED = STATUSES[STATUSES.length - 1].label;
const CANDIDATE = stripEmoji(STATUSES[0].label);                                   // '촬영 후보'
const isCandidateStatus = (s) => s === CANDIDATE || STATUSES[0].legacy.includes(s); // 옛 라벨 '후보' 도 후보로 본다

const PLATFORMS = [
  { key: 'youtube',   label: '유튜브',     emoji: '▶️' },
  { key: 'instagram', label: '인스타그램', emoji: '📸' },
  { key: 'tiktok',    label: '틱톡',       emoji: '🎵' },
  { key: 'naverClip', label: '네이버 클립', emoji: '🟢' },
];

// 열 정의. 배열 순서가 곧 열 순서(A, B, C …)다. 열을 추가/이동할 때 여기만 고치면 된다.
// group: 1행 그룹 띠. 같은 group 이 이어진 열들이 하나로 합쳐진다.
const COLUMNS = [
  { key: 'status',      header: '📌 상태',        width: 160, group: 'basic' },
  { key: 'dish',        header: '🍳 요리 제목',    width: 180, group: 'basic', wrap: true },
  { key: 'srcUrl',      header: '🔗 원본 링크',    width: 200, group: 'source', note: '유튜브 링크를 붙이면 원본 제목·채널이 자동으로 채워집니다.' },
  { key: 'srcTitle',    header: '🎞️ 원본 제목',    width: 240, group: 'source', wrap: true },
  { key: 'srcChannel',  header: '📺 원본 채널',    width: 120, group: 'source' },
  { key: 'thumb',       header: '🖼️ 썸네일',       width: 110, group: 'source', note: '내가 재가공한 쇼츠의 썸네일. 웹 현황판(/shorts) 또는 업로드 헬퍼의 썸네일 만들기에서 등록합니다.' },
  { key: 'refUrls',     header: '🔍 참고 쇼츠 링크', width: 220, group: 'ref', wrap: true, note: '여러 개는 줄바꿈(⌥⏎ / Alt+Enter)으로 한 줄에 하나씩. 참고 채널이 같은 순서로 채워집니다.' },
  { key: 'refChannels', header: '👥 참고 채널',    width: 120, group: 'ref', wrap: true },
  ...PLATFORMS.flatMap(p => [
    { key: p.key + 'On',  header: `${p.emoji} ${p.label}`,  width: 90,  group: 'platform', checkbox: true, platform: true },
    { key: p.key + 'Url', header: `🔗 ${p.label} 링크`,     width: 160, group: 'platform', platform: true },
  ]),
  { key: 'title',       header: '✏️ 영상 제목',    width: 240, group: 'video', wrap: true },
  { key: 'titleLen',    header: '🔢 제목 글자수',  width: 84,  group: 'video', lenOf: 'title', limit: 100 },
  { key: 'desc',        header: '📝 설명',         width: 320, group: 'video', wrap: true },
  { key: 'pinned',      header: '💬 고정 댓글',    width: 240, group: 'video', wrap: true },
  { key: 'memo',        header: '🗒️ 메모',         width: 200, group: 'etc', wrap: true },
  { key: 'updatedAt',   header: '🕒 수정일',       width: 130, group: 'etc', date: true },
  { key: 'createdAt',   header: '📅 등록일',       width: 130, group: 'etc', date: true },
  // 숨김 열: 썸네일 이미지의 URL(텍스트). IMAGE() 수식은 CSV 로 내보내면 빈 칸이 되므로 웹 현황판은 이 열을 읽는다.
  { key: 'thumbUrl',    header: '🔗 썸네일 링크',  width: 60,  group: 'etc', hidden: true, note: '웹 현황판이 쓰는 칸입니다. 직접 고치지 마세요.' },
];
// 그룹 띠 색: strong = 1행 배경(흰 글자), light/dark = 2행 열 이름 배경/글자
const GROUPS = {
  basic:    { label: '📋 기본',          strong: '#4f46e5', light: '#e0e7ff', dark: '#3730a3' },
  source:   { label: '🎬 원본 영상',     strong: '#0284c7', light: '#e0f2fe', dark: '#075985' },
  ref:      { label: '🔎 참고 쇼츠',     strong: '#0d9488', light: '#ccfbf1', dark: '#115e59' },
  platform: { label: '🚀 업로드 플랫폼', strong: '#9333ea', light: '#f3e8ff', dark: '#6b21a8' },
  video:    { label: '✍️ 내 영상 정보',  strong: '#d97706', light: '#fef3c7', dark: '#92400e' },
  etc:      { label: '🗂️ 기타',          strong: '#64748b', light: '#f1f5f9', dark: '#334155' },
};
const REMOVED_HEADERS = ['설명 글자수'];   // 이전 버전에 있었지만 지운 열 (헤더 이름으로 찾아 삭제)

// 레퍼 체크(로컬 앱 /references) 팀 동기화 탭. 각 팀원의 로컬 앱이 웹 앱을 통해 읽고 쓴다. 1행이 헤더, 2행부터 데이터.
// '레퍼 채널'은 사람이 보는 탭이라 프로필 이미지·링크·정렬 필터를 갖추고, '레퍼 쇼츠'는 데이터 전용 탭이다.
const REF_CHANNELS_NAME = '레퍼 채널';
const REF_SHORTS_NAME = '레퍼 쇼츠';
const REF_FIRST_DATA_ROW = 2;
const REF_ROW_HEIGHT = 56;
const REF_NOTE = '레퍼 체크(/references)가 자동으로 채우는 탭입니다. 헤더의 필터 버튼으로 정렬만 하고 값은 직접 고치지 마세요.';
const REF_THEME = { head_bg: '#0d9488', head_fg: '#ffffff' };   // 현황판 '참고 쇼츠' 그룹과 같은 청록
// 열 속성: number 숫자 · date 날짜(로컬 앱과는 ISO 문자열로 주고받음) · flag 이모지 상태 · image 다른 열(URL)의 이미지 표시 ·
//          link 다른 키(URL)를 셀 텍스트에 링크 · hidden 숨김(앱만 쓰는 값) · 나머지는 텍스트(@ 서식으로 자동 변환 방지)
const REF_CHANNEL_COLUMNS = [
  { key: 'thumb',             header: '🖼️ 프로필',          width: 70,  image: 'thumbnail', align: 'center' },
  { key: 'name',              header: '📺 채널',            width: 230, link: 'url', bold: true },
  { key: 'subscriber_count',  header: '👥 구독자',          width: 100, number: true, align: 'right' },
  { key: 'added_at',          header: '📅 추가 시각',       width: 140, date: true, align: 'center' },
  { key: 'shorts_complete',   header: '📦 쇼츠 목록',       width: 110, flag: true, align: 'center' },
  { key: 'shorts_updated_at', header: '🕒 분석 저장 시각',  width: 140, date: true, align: 'center' },
  { key: 'thumbnail',         header: '🔗 프로필 이미지 URL', width: 60, hidden: true },
  { key: 'description',       header: '📝 채널 설명',       width: 60,  hidden: true },
  { key: 'id',                header: '🆔 채널 ID',         width: 230, muted: true },
];
// 이전 배치의 헤더 이름 → 키. setupSheets 가 옛 배치를 발견하면 이 표로 읽어 새 배치로 옮긴다.
const REF_CHANNEL_LEGACY_HEADERS = {
  '채널 ID': 'id', '채널 이름': 'name', '채널 URL': 'url', '프로필 이미지': 'thumbnail', '채널 설명': 'description',
  '구독자': 'subscriber_count', '추가 시각': 'added_at', '목록 수집 완료': 'shorts_complete', '분석 저장 시각': 'shorts_updated_at',
};
const REF_FLAGS = [   // 쇼츠 목록 수집 상태 (flag 열). 첫 글자(이모지)로 판별한다.
  { label: '✅ 완료',   bg: '#dcfce7', fg: '#15803d' },
  { label: '⏳ 진행 중', bg: '#fef3c7', fg: '#b45309' },
  { label: '➖ 미분석', bg: '#f1f5f9', fg: '#64748b' },
];
// 쇼츠 상세 수집 상태 (detail 열). key 는 로컬 앱이 쓰는 값, label 은 셀에 보이는 배지.
const REF_DETAIL_STATUSES = [
  { key: 'complete', label: '✅ 분석 완료', bg: '#dcfce7', fg: '#15803d' },
  { key: 'pending',  label: '⏳ 상세 대기', bg: '#fef3c7', fg: '#b45309' },
  { key: 'skipped',  label: '➖ 50만 미만', bg: '#f1f5f9', fg: '#64748b' },
  { key: 'error',    label: '⚠️ 조회 실패', bg: '#fee2e2', fg: '#b91c1c' },
];
// 레퍼 쇼츠: 채널의 쇼츠 한 줄. '📌 현황판' 열은 현황판 탭의 같은 영상 상태를 비추며, 이 탭에서는 ⭐ 촬영 후보 지정/해제만 할 수 있다.
const REF_SHORT_COLUMNS = [
  { key: 'thumb',                 header: '🖼️ 썸네일',          width: 70,  image: 'thumbnail', align: 'center' },
  { key: 'title',                 header: '🎬 제목',            width: 300, link: 'url', bold: true },
  { key: 'view_count',            header: '👁️ 조회수',          width: 95,  number: true, align: 'right' },
  { key: 'like_count',            header: '👍 좋아요',          width: 85,  number: true, align: 'right' },
  { key: 'comment_count',         header: '💬 댓글',            width: 75,  number: true, align: 'right' },
  { key: 'upload_date',           header: '📅 업로드일',        width: 100, ymd: true, align: 'center' },
  { key: 'detail_status',         header: '🔎 상세 상태',       width: 110, detail: true, align: 'center', badges: REF_DETAIL_STATUSES },
  { key: 'board_status',          header: '📌 현황판',          width: 125, board: true, align: 'center', badges: STATUSES },
  { key: 'pinned_comment_text',   header: '📍 고정 댓글',       width: 240 },
  { key: 'pinned_comment_author', header: '✍️ 고정 댓글 작성자', width: 120, muted: true },
  { key: 'description',           header: '📝 설명',            width: 260 },
  { key: 'detail_error',          header: '⚠️ 상세 오류',       width: 160, muted: true },
  { key: 'synced_at',             header: '🕒 동기화 시각',     width: 130, date: true, align: 'center' },
  { key: 'thumbnail',             header: '🔗 썸네일 URL',      width: 60,  hidden: true },
  { key: 'channel_id',            header: '📺 채널 ID',         width: 200, muted: true },
  { key: 'video_id',              header: '🎞️ 영상 ID',         width: 110, muted: true },
];
const REF_SHORT_LEGACY_HEADERS = {
  '채널 ID': 'channel_id', '영상 ID': 'video_id', '제목': 'title', 'URL': 'url', '썸네일 URL': 'thumbnail', '조회수': 'view_count',
  '좋아요': 'like_count', '댓글 수': 'comment_count', '업로드일': 'upload_date', '고정 댓글': 'pinned_comment_text',
  '고정 댓글 작성자': 'pinned_comment_author', '상세 상태': 'detail_status', '상세 오류': 'detail_error', '설명': 'description', '동기화 시각': 'synced_at',
};
const REF_BOARD_HELP = '이 탭에서는 ⭐ 촬영 후보 지정/해제만 할 수 있습니다. 다른 단계는 현황판 탭에서 바꿉니다.';

const COL = Object.fromEntries(COLUMNS.map((c, i) => [c.key, i + 1]));  // key → 1부터 시작하는 열 번호
const LAST_COL = COLUMNS.length;
const PLATFORM_FIRST_COL = COL[PLATFORMS[0].key + 'On'];
const PLATFORM_LAST_COL = COL[PLATFORMS[PLATFORMS.length - 1].key + 'Url'];

const PALETTE = {
  text: '#1f2937', muted: '#64748b', line: '#e2e8f0', band: '#f8fafc',
  danger_bg: '#fee2e2', danger_fg: '#b91c1c', lock_bg: '#f1f5f9', lock_fg: '#a1a1aa', check_bg: '#dcfce7', check_fg: '#15803d',
};

function colLetter(n) {
  let s = '';
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

// ---------------------------------------------------------------------------
// 메뉴
// ---------------------------------------------------------------------------
function onOpen() {
  SpreadsheetApp.getUi().createMenu('쇼츠 현황판')
    .addItem('🎨 시트 초기 설정 (다시 실행해도 안전)', 'setupSheets')
    .addSeparator()
    .addItem('🔄 선택한 행 정보 다시 가져오기', 'fillSelectedRows')
    .addItem('✨ 빈 정보 모두 채우기', 'fillAllMissing')
    .addSeparator()
    .addItem('🔑 썸네일 업로드 연결 정보', 'showUploadInfo')
    .addItem('♻️ 업로드 토큰 다시 만들기', 'regenerateUploadToken')
    .addToUi();
}

// ---------------------------------------------------------------------------
// 초기 설정
// ---------------------------------------------------------------------------
function setupSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    const sheets = ss.getSheets();
    if (sheets.length === 1 && sheets[0].getLastRow() === 0) {
      sheet = sheets[0];               // 새 문서의 빈 기본 시트를 재활용
      sheet.setName(SHEET_NAME);
    } else {
      sheet = ss.insertSheet(SHEET_NAME, 0);
    }
  }
  migrateLayout(sheet);
  setupBoardSheet(sheet);
  setupSummarySheet(ss);
  setupReferenceSheets(ss);
  installEditTrigger(ss);
  thumbFolder();   // 드라이브의 썸네일 폴더를 미리 만든다 (여기서 드라이브 권한 승인이 뜬다). 이미 있으면 그대로 둔다.
  ss.setActiveSheet(sheet);
  ss.toast('설정 완료. 원본 링크를 붙이면 제목·채널이 자동으로 채워집니다. 썸네일은 웹 현황판에서 등록합니다.', '쇼츠 현황판', 8);
}

/** 이전 버전 배치를 새 배치로 옮긴다. 데이터는 지우지 않는다. */
function migrateLayout(sheet) {
  const lastCol = Math.max(sheet.getLastColumn(), 1);
  // v1: 열 이름이 1행에 있었고 그룹 띠가 없었다 → 위에 한 행 삽입
  const a1 = stripEmoji(sheet.getRange(1, 1).getValue());
  if (a1 === stripEmoji(COLUMNS[0].header)) sheet.insertRowBefore(1);

  // 지운 열 제거 (헤더 이름으로 찾음)
  let hdr = sheet.getRange(HEADER_ROW, 1, 1, lastCol).getValues()[0].map(headerKeyName);
  for (let i = hdr.length - 1; i >= 0; i--) {
    if (REMOVED_HEADERS.includes(hdr[i])) sheet.deleteColumn(i + 1);
  }
  const hadThumbUrlCol = hdr.includes(headerKeyName(COLUMNS[COL.thumbUrl - 1].header));

  // 열 순서 맞추기. 사용자가 시트에서 열을 드래그해 순서를 바꿨어도 웹 현황판은 헤더 이름으로 읽기 때문에 문제가 없지만,
  // 아래 setupBoardSheet 가 2행 헤더를 표준 순서로 다시 쓰므로 그 전에 실제 열을 (헤더 이름 기준으로) 표준 순서로 옮겨 둔다.
  // 그렇지 않으면 헤더와 데이터가 어긋난다. 코드에 없는 열(직접 추가한 열)은 뒤쪽에 그대로 남는다.
  reorderColumns(sheet);
  hdr = sheet.getRange(HEADER_ROW, 1, 1, sheet.getMaxColumns()).getValues()[0].map(headerKeyName);

  // 상태값 라벨 변경 (이모지 없는 예전 라벨 → 새 라벨)
  const lastRow = sheet.getLastRow();
  if (lastRow >= FIRST_DATA_ROW) {
    const rng = sheet.getRange(FIRST_DATA_ROW, COL.status, lastRow - FIRST_DATA_ROW + 1, 1);
    const vals = rng.getValues();
    let changed = false;
    vals.forEach(r => {
      const raw = String(r[0]).trim(), key = stripEmoji(raw);
      if (!key) return;
      const hit = STATUSES.find(s => stripEmoji(s.label) === key || s.legacy.includes(key));
      if (hit && hit.label !== raw) { r[0] = hit.label; changed = true; }
    });
    // 예전 드롭다운 규칙(옛 라벨만 허용, 잘못된 값 거부)이 남아 있으면 새 라벨 쓰기가 거부되므로 먼저 걷어낸다.
    if (changed) { rng.clearDataValidations(); rng.setValues(vals); }
  }

  // v3: 썸네일 열은 원본 영상 썸네일이 아니라 내 쇼츠 썸네일 자리. 예전 버전이 자동으로 넣은
  // 유튜브 썸네일 IMAGE() 수식을 비운다. '썸네일 링크' 열이 아직 없을 때(= 처음 v3 로 올릴 때) 한 번만 실행되므로
  // 새 방식으로 올린 드라이브 이미지는 건드리지 않는다.
  if (!hadThumbUrlCol && lastRow >= FIRST_DATA_ROW) {
    const rng = sheet.getRange(FIRST_DATA_ROW, COL.thumb, lastRow - FIRST_DATA_ROW + 1, 1);
    // 이 열에 잘못 복사된 검증 규칙(예: 상태 드롭다운)이 있으면 clearContent 도 거부된다 → 먼저 제거
    rng.clearDataValidations();
    rng.getFormulas().forEach((r, i) => {
      if (SOURCE_THUMB_RE.test(r[0])) sheet.getRange(FIRST_DATA_ROW + i, COL.thumb).clearContent();
    });
  }
}

/** 헤더 텍스트 → 비교용 이름. '📌 상태' → '상태', '유튜브 ☑'(v1) → '유튜브'. */
function headerKeyName(h) {
  return stripEmoji(h).replace(/\s*☑\s*$/, '').trim();
}

/**
 * 2행 헤더 이름을 보고 실제 열을 COLUMNS 순서대로 옮긴다. 없는 열(새로 추가된 열)은 그 자리에 삽입한다.
 * 헤더 이름이 코드와 일치하지 않는 열은 표준 열 뒤로 밀린다(지우지 않음).
 */
function reorderColumns(sheet) {
  const wanted = COLUMNS.map(c => headerKeyName(c.header));
  // 1행 그룹 띠의 병합은 열 이동을 방해하므로 먼저 푼다 (setupBoardSheet 가 다시 합친다)
  sheet.getRange(GROUP_ROW, 1, 1, sheet.getMaxColumns()).breakApart();
  for (let target = 1; target <= wanted.length; target++) {
    const names = sheet.getRange(HEADER_ROW, 1, 1, sheet.getMaxColumns()).getValues()[0].map(headerKeyName);
    // target 앞은 이미 정리됐으니 target 이후에서만 찾는다 (같은 이름이 둘이면 첫 번째)
    let cur = names.indexOf(wanted[target - 1], target - 1) + 1;
    if (cur === 0) {
      // 아직 없는 열 → 그 자리에 빈 열 삽입 (헤더는 setupBoardSheet 가 쓴다)
      if (target <= sheet.getMaxColumns()) sheet.insertColumnBefore(target); else sheet.insertColumnsAfter(sheet.getMaxColumns(), 1);
      continue;
    }
    if (cur !== target) sheet.moveColumns(sheet.getRange(1, cur), target);   // 왼쪽으로 옮기므로 정확히 target 위치에 놓인다
  }
}

function setupBoardSheet(sheet) {
  // 열·행 개수 확보
  if (sheet.getMaxColumns() < LAST_COL) sheet.insertColumnsAfter(sheet.getMaxColumns(), LAST_COL - sheet.getMaxColumns());
  if (sheet.getMaxRows() < MIN_ROWS) sheet.insertRowsAfter(sheet.getMaxRows(), MIN_ROWS - sheet.getMaxRows());
  const maxRows = sheet.getMaxRows();
  const dataRows = maxRows - FIRST_DATA_ROW + 1;

  sheet.setHiddenGridlines(true);
  sheet.getBandings().forEach(b => b.remove());
  sheet.getRange(GROUP_ROW, 1, 1, LAST_COL).breakApart();

  // 1행: 그룹 띠 (같은 그룹의 연속 열을 합침)
  const groupRow = sheet.getRange(GROUP_ROW, 1, 1, LAST_COL);
  groupRow.clearContent().clearFormat();
  let start = 1;
  for (let i = 1; i <= LAST_COL; i++) {
    const cur = COLUMNS[i - 1].group;
    const next = i < LAST_COL ? COLUMNS[i].group : null;
    if (cur !== next) {
      const g = GROUPS[cur];
      const rng = sheet.getRange(GROUP_ROW, start, 1, i - start + 1);
      if (i > start) rng.merge();
      rng.setValue(g.label).setBackground(g.strong).setFontColor('#ffffff').setFontWeight('bold').setFontSize(11)
        .setHorizontalAlignment('center').setVerticalAlignment('middle');
      start = i + 1;
    }
  }
  sheet.setRowHeight(GROUP_ROW, 30);

  // 2행: 열 이름 (그룹 색의 연한 톤)
  COLUMNS.forEach((c, i) => {
    const g = GROUPS[c.group];
    const cell = sheet.getRange(HEADER_ROW, i + 1);
    cell.setValue(c.header).setBackground(g.light).setFontColor(g.dark).setFontWeight('bold').setFontSize(10)
      .setHorizontalAlignment('center').setVerticalAlignment('middle').setWrap(false);
    if (c.note) cell.setNote(c.note); else cell.clearNote();
    sheet.setColumnWidth(i + 1, c.width);
    if (c.hidden) sheet.hideColumns(i + 1); else sheet.showColumns(i + 1);
  });
  sheet.getRange(HEADER_ROW, 1, 1, LAST_COL)
    .setBorder(null, null, true, null, null, null, PALETTE.line, SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  sheet.setRowHeight(HEADER_ROW, 36);
  sheet.setFrozenRows(HEADER_ROW);
  sheet.setFrozenColumns(2);   // 상태 + 요리 제목 고정
  ensureBoardFilter(sheet);

  // 데이터 영역 공통 서식: 줄무늬, 얇은 가로선, 세로 가운데
  const body = sheet.getRange(FIRST_DATA_ROW, 1, dataRows, LAST_COL);
  body.setVerticalAlignment('middle').setFontColor(PALETTE.text).setFontSize(10)
    .setBorder(null, null, null, null, null, true, PALETTE.line, SpreadsheetApp.BorderStyle.SOLID);
  const banding = body.applyRowBanding(SpreadsheetApp.BandingTheme.LIGHT_GREY, false, false);
  banding.setFirstRowColor('#ffffff').setSecondRowColor(PALETTE.band);
  COLUMNS.forEach((c, i) => {
    const rng = sheet.getRange(FIRST_DATA_ROW, i + 1, dataRows, 1);
    rng.setWrapStrategy(c.wrap ? SpreadsheetApp.WrapStrategy.WRAP : SpreadsheetApp.WrapStrategy.CLIP);
    rng.setHorizontalAlignment(c.checkbox || c.lenOf || c.date || c.key === 'status' || c.key === 'thumb' ? 'center' : 'left');
    if (c.date) rng.setNumberFormat('yyyy-mm-dd hh:mm').setFontColor(PALETTE.muted).setFontSize(9);
    if (c.lenOf) rng.setFontColor(PALETTE.muted);
    if (c.key === 'status') rng.setFontWeight('bold');
    if (c.key === 'dish') rng.setFontWeight('bold');
  });
  sheet.setRowHeightsForced(FIRST_DATA_ROW, dataRows, ROW_HEIGHT);

  // 데이터 확인: 상태 드롭다운, 플랫폼 체크박스
  // 먼저 데이터 영역의 검증 규칙을 모두 지운다. 예전 버전 규칙이나 셀 복사로 다른 열에 번진 규칙이 남아 있으면
  // 스크립트의 셀 쓰기(썸네일 등록, 상태 라벨 변경)가 "검증 위반"으로 거부된다.
  body.clearDataValidations();
  const statusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(STATUSES.map(s => s.label), true).setAllowInvalid(false)
    .setHelpText(STATUSES.map(s => s.label).join(' → ')).build();
  sheet.getRange(FIRST_DATA_ROW, COL.status, dataRows, 1).setDataValidation(statusRule);
  const checkboxRule = SpreadsheetApp.newDataValidation().requireCheckbox().build();
  COLUMNS.forEach((c, i) => { if (c.checkbox) sheet.getRange(FIRST_DATA_ROW, i + 1, dataRows, 1).setDataValidation(checkboxRule); });

  // 글자수 열: 첫 데이터 행에 ARRAYFORMULA 하나로 전체 계산 (아래 셀은 비워 둬야 하므로 정리)
  COLUMNS.forEach((c, i) => {
    if (!c.lenOf) return;
    const src = colLetter(COL[c.lenOf]);
    if (dataRows > 1) sheet.getRange(FIRST_DATA_ROW + 1, i + 1, dataRows - 1, 1).clearContent();
    sheet.getRange(FIRST_DATA_ROW, i + 1)
      .setFormula(`=ARRAYFORMULA(IF(${src}${FIRST_DATA_ROW}:${src}="","",LEN(${src}${FIRST_DATA_ROW}:${src})))`);
  });

  // 조건부 서식 (이 시트의 규칙을 통째로 다시 만든다. 앞에 있는 규칙이 우선)
  const rules = [];
  const statusRange = sheet.getRange(FIRST_DATA_ROW, COL.status, dataRows, 1);
  STATUSES.forEach(s => {
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo(s.label).setBackground(s.bg).setFontColor(s.fg).setBold(true)
      .setRanges([statusRange]).build());
  });
  const platRange = sheet.getRange(FIRST_DATA_ROW, PLATFORM_FIRST_COL, dataRows, PLATFORM_LAST_COL - PLATFORM_FIRST_COL + 1);
  rules.push(SpreadsheetApp.newConditionalFormatRule()   // 업로드 완료가 아니면 잠금 표시
    .whenFormulaSatisfied(`=$${colLetter(COL.status)}${FIRST_DATA_ROW}<>"${UPLOADED}"`)
    .setBackground(PALETTE.lock_bg).setFontColor(PALETTE.lock_fg)
    .setRanges([platRange]).build());
  COLUMNS.forEach((c, i) => {                            // 체크된 플랫폼은 초록으로
    if (!c.checkbox) return;
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied(`=${colLetter(i + 1)}${FIRST_DATA_ROW}=TRUE`)
      .setBackground(PALETTE.check_bg).setFontColor(PALETTE.check_fg)
      .setRanges([sheet.getRange(FIRST_DATA_ROW, i + 1, dataRows, 1)]).build());
  });
  COLUMNS.forEach((c, i) => {                            // 글자수 초과
    if (!c.limit) return;
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenNumberGreaterThan(c.limit).setBackground(PALETTE.danger_bg).setFontColor(PALETTE.danger_fg).setBold(true)
      .setRanges([sheet.getRange(FIRST_DATA_ROW, i + 1, dataRows, 1)]).build());
  });
  sheet.setConditionalFormatRules(rules);
}

/** 기본 필터를 전체 현황판 범위로 맞춘다. 기존 열별 조건은 보존한다. */
function ensureBoardFilter(sheet) {
  const old = sheet.getFilter();
  const criteria = {};
  if (old) {
    for (let c = 1; c <= Math.min(old.getRange().getNumColumns(), LAST_COL); c++) {
      const criterion = old.getColumnFilterCriteria(c);
      if (criterion) criteria[c] = criterion;
    }
    old.remove();
  }
  const filter = sheet.getRange(HEADER_ROW, 1, sheet.getMaxRows() - HEADER_ROW + 1, LAST_COL).createFilter();
  Object.keys(criteria).forEach(c => filter.setColumnFilterCriteria(Number(c), criteria[c]));
}

function setupSummarySheet(ss) {
  let sheet = ss.getSheetByName(SUMMARY_NAME);
  if (!sheet) sheet = ss.insertSheet(SUMMARY_NAME, 1);
  sheet.clear();
  sheet.getBandings().forEach(b => b.remove());
  sheet.setHiddenGridlines(true);
  const b = `'${SHEET_NAME}'`;
  const st = colLetter(COL.status), dish = colLetter(COL.dish);

  const rows = [];                    // [라벨, 값 또는 수식, 종류]
  const push = (a, v, kind) => rows.push([a, v, kind]);
  push('📊 쇼츠 현황 요약', '', 'title');
  push('', '', 'gap');
  push('📌 상태별', '개수', 'head:basic');
  STATUSES.forEach(s => push(s.label, `=COUNTIF(${b}!$${st}$${FIRST_DATA_ROW}:$${st}, A${rows.length + 1})`, 'row'));
  push('전체 항목', `=COUNTA(${b}!$${dish}$${FIRST_DATA_ROW}:$${dish})`, 'total');
  push('', '', 'gap');
  push('🚀 플랫폼별 업로드', '개수', 'head:platform');
  PLATFORMS.forEach(p => {
    const c = colLetter(COL[p.key + 'On']);
    push(`${p.emoji} ${p.label}`, `=COUNTIF(${b}!$${c}$${FIRST_DATA_ROW}:$${c}, TRUE)`, 'row');
  });

  sheet.getRange(1, 1, rows.length, 2).setValues(rows.map(r => [r[0], r[1]]))
    .setFontColor(PALETTE.text).setFontSize(11).setVerticalAlignment('middle');
  sheet.setColumnWidth(1, 240); sheet.setColumnWidth(2, 90);
  rows.forEach((r, i) => {
    const line = sheet.getRange(i + 1, 1, 1, 2);
    const kind = r[2];
    if (kind === 'title') { line.merge().setFontSize(16).setFontWeight('bold'); sheet.setRowHeight(i + 1, 44); }
    else if (kind.startsWith('head:')) {
      const g = GROUPS[kind.split(':')[1]];
      line.setBackground(g.strong).setFontColor('#ffffff').setFontWeight('bold'); sheet.setRowHeight(i + 1, 32);
    }
    else if (kind === 'row' || kind === 'total') {
      line.setBorder(null, null, true, null, null, null, PALETTE.line, SpreadsheetApp.BorderStyle.SOLID);
      sheet.getRange(i + 1, 2).setFontWeight('bold').setFontSize(13);
      if (kind === 'total') line.setBackground(PALETTE.band).setFontWeight('bold');
      sheet.setRowHeight(i + 1, 30);
    }
  });
  sheet.getRange(1, 2, rows.length, 1).setHorizontalAlignment('center');
  STATUSES.forEach((s, k) => {                          // 상태 라벨 셀에 상태 색
    const rowIdx = rows.findIndex(r => r[0] === s.label) + 1;
    if (rowIdx > 0) sheet.getRange(rowIdx, 1).setBackground(s.bg).setFontColor(s.fg).setFontWeight('bold');
  });
}

function installEditTrigger(ss) {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'handleEdit')
    .forEach(t => ScriptApp.deleteTrigger(t));
  // 단순 onEdit 트리거는 UrlFetchApp 을 못 쓰므로 설치형 트리거를 건다
  ScriptApp.newTrigger('handleEdit').forSpreadsheet(ss).onEdit().create();
}

// ---------------------------------------------------------------------------
// 편집 트리거
// ---------------------------------------------------------------------------
function handleEdit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (sheet.getName() === REF_SHORTS_NAME) return handleRefShortsEdit(e);
  if (sheet.getName() !== SHEET_NAME) return;
  const top = e.range.getRow(), left = e.range.getColumn();
  const numRows = e.range.getNumRows(), numCols = e.range.getNumColumns();
  if (top + numRows - 1 < FIRST_DATA_ROW) return;   // 헤더만 편집
  const single = numRows === 1 && numCols === 1;
  const now = new Date();
  const affectedVideos = new Set();   // 상태·링크가 바뀐 행의 영상 ID → 레퍼 쇼츠 탭의 현황판 열에 반영

  for (let r = Math.max(top, FIRST_DATA_ROW); r < top + numRows; r++) {
    let touched = false;
    for (let c = left; c < left + numCols; c++) {
      if (c > LAST_COL) continue;
      const def = COLUMNS[c - 1];
      if (def.key === 'updatedAt' || def.key === 'createdAt' || def.lenOf) continue;

      if (def.platform && !isUploaded(sheet, r)) {
        revertPlatformCell(sheet, r, c, def, single ? e.oldValue : undefined);
        continue;   // 되돌린 편집은 수정일을 갱신하지 않음
      }
      if (def.key === 'srcUrl') fillSource(sheet, r, true);
      if (def.key === 'refUrls') fillRefs(sheet, r, true);
      if (def.key === 'status' || def.key === 'refUrls' || def.key === 'srcUrl') {
        boardRowVideoIds(sheet, r).forEach(v => affectedVideos.add(v));
        if (single && e.oldValue) allVideoIds(e.oldValue).forEach(v => affectedVideos.add(v));   // 링크를 지운 영상은 미등록으로
      }
      touched = true;
    }
    if (touched) touchRow(sheet, r, now);
  }
  if (affectedVideos.size) refSyncBoardVideos(sheet.getParent(), [...affectedVideos]);
}

/** 레퍼 쇼츠 탭의 '📌 현황판' 열 편집: ⭐ 촬영 후보 지정 → 현황판에 후보 행 생성, 비우기 → 후보 행 삭제. 그 외는 되돌린다. */
function handleRefShortsEdit(e) {
  const sheet = e.range.getSheet();
  const boardCol = REF_SHORT_COLUMNS.findIndex(c => c.board) + 1;
  const top = e.range.getRow(), left = e.range.getColumn();
  const numRows = e.range.getNumRows(), numCols = e.range.getNumColumns();
  if (top + numRows - 1 < REF_FIRST_DATA_ROW || boardCol < left || boardCol >= left + numCols) return;
  const ss = sheet.getParent();
  const board = ss.getSheetByName(SHEET_NAME);
  if (!board) return;
  const toast = (msg) => ss.toast(msg, '레퍼 쇼츠', 6);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) { toast('시트가 다른 작업을 처리 중입니다. 잠시 후 다시 시도해 주세요.'); return; }
  try {
    for (let r = Math.max(top, REF_FIRST_DATA_ROW); r < top + numRows; r++) {
      const wanted = stripEmoji(sheet.getRange(r, boardCol).getValue());
      const flat = refRowItem(sheet, REF_SHORT_COLUMNS, r);
      const vid = flat.video_id;
      if (!vid) { sheet.getRange(r, boardCol).setValue(''); continue; }
      const existing = candidateRows(board).find(x => x.videoId === vid);
      const current = existing ? boardLabelOf(existing.label) : '';
      if (existing && !isCandidateStatus(existing.status)) {
        refSyncBoardVideos(ss, [vid]);
        if (stripEmoji(current) !== wanted) toast(`현황판에서 이미 '${current}' 단계라 이 탭에서는 바꿀 수 없어요.`);
        continue;
      }
      if (!wanted) {                                   // 해제: 현황판의 후보 행을 지운다
        if (existing) board.deleteRow(existing.row);
        refSyncBoardVideos(ss, [vid]);
        continue;
      }
      if (isCandidateStatus(wanted)) {                 // 지정: 현황판에 후보 행을 만든다
        if (!existing) boardAddCandidate(board, vid, flat.title, refChannelName(ss, flat.channel_id));
        refSyncBoardVideos(ss, [vid]);
        continue;
      }
      refSyncBoardVideos(ss, [vid]);                   // 다른 단계로 바꾸려 한 경우: 되돌린다
      toast(REF_BOARD_HELP);
    }
  } finally {
    lock.releaseLock();
  }
}

function isUploaded(sheet, row) {
  return String(sheet.getRange(row, COL.status).getValue()).trim() === UPLOADED;
}

function revertPlatformCell(sheet, row, col, def, oldValue) {
  const cell = sheet.getRange(row, col);
  if (def.checkbox) {
    if (cell.getValue() === true) cell.setValue(false);
  } else if (String(cell.getValue()).trim() !== '') {
    cell.setValue(oldValue !== undefined ? oldValue : '');
  } else {
    return;   // 비우는 편집은 그대로 둔다
  }
  SpreadsheetApp.getActiveSpreadsheet().toast(
    `업로드 플랫폼은 상태가 '${UPLOADED}'일 때만 입력할 수 있어요. (${row}행)`, '쇼츠 현황판', 5);
}

function touchRow(sheet, row, now) {
  sheet.getRange(row, COL.updatedAt).setValue(now);
  const created = sheet.getRange(row, COL.createdAt);
  const dish = String(sheet.getRange(row, COL.dish).getValue()).trim();
  if (dish && !created.getValue()) created.setValue(now);
}

// ---------------------------------------------------------------------------
// 유튜브 정보 (oEmbed: API 키 불필요. 비공개·삭제 영상은 실패 → null)
// ---------------------------------------------------------------------------
function fetchMeta(url) {
  url = String(url || '').trim();
  if (!/youtube\.com|youtu\.be/.test(url)) return null;
  try {
    const res = UrlFetchApp.fetch(
      'https://www.youtube.com/oembed?url=' + encodeURIComponent(url) + '&format=json',
      { muteHttpExceptions: true, followRedirects: true });
    if (res.getResponseCode() !== 200) return null;
    const d = JSON.parse(res.getContentText());
    return { title: d.title || '', channel: d.author_name || '', thumbnail: d.thumbnail_url || '' };
  } catch (err) {
    return null;
  }
}

/** 원본 링크 → 원본 제목·채널. (썸네일 열은 내 쇼츠 썸네일 자리라 건드리지 않는다.) force=false 면 비어 있는 칸만 채운다. 채웠으면 true. */
function fillSource(sheet, row, force) {
  const url = String(sheet.getRange(row, COL.srcUrl).getValue()).trim();
  const targets = sheet.getRange(row, COL.srcTitle, 1, 2);   // srcTitle, srcChannel (연속 2열)
  if (!url) { targets.clearContent(); return false; }
  if (!force && targets.getValues()[0].every(v => String(v).trim() !== '')) return false;
  const m = fetchMeta(url);
  if (!m) {
    SpreadsheetApp.getActiveSpreadsheet().toast(`원본 정보를 가져올 수 없어요. 링크를 확인해 주세요. (${row}행)`, '쇼츠 현황판', 5);
    return false;
  }
  sheet.getRange(row, COL.srcTitle).setValue(m.title);
  sheet.getRange(row, COL.srcChannel).setValue(m.channel);
  return true;
}

/** 참고 쇼츠 링크(여러 줄) → 참고 채널(같은 순서, 여러 줄). */
function fillRefs(sheet, row, force) {
  const raw = String(sheet.getRange(row, COL.refUrls).getValue());
  const target = sheet.getRange(row, COL.refChannels);
  const urls = raw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (!urls.length) { target.clearContent(); return false; }
  if (!force && String(target.getValue()).trim() !== '') return false;
  const names = urls.map(u => { const m = fetchMeta(u); return m ? m.channel : '(확인 불가)'; });
  target.setValue(names.join('\n'));
  return true;
}

function fillSelectedRows() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getActiveSheet();
  if (sheet.getName() !== SHEET_NAME) { ss.toast(`'${SHEET_NAME}' 시트에서 행을 선택해 주세요.`, '쇼츠 현황판', 5); return; }
  const rng = sheet.getActiveRange();
  let n = 0;
  for (let r = Math.max(rng.getRow(), FIRST_DATA_ROW); r < rng.getRow() + rng.getNumRows(); r++) {
    if (fillSource(sheet, r, true)) n++;
    if (fillRefs(sheet, r, true)) n++;
  }
  ss.toast(`${n}개 항목을 다시 가져왔어요.`, '쇼츠 현황판', 5);
}

function fillAllMissing() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) { ss.toast('먼저 시트 초기 설정을 실행해 주세요.', '쇼츠 현황판', 5); return; }
  const last = sheet.getLastRow();
  let n = 0;
  for (let r = FIRST_DATA_ROW; r <= last; r++) {
    if (fillSource(sheet, r, false)) n++;
    if (fillRefs(sheet, r, false)) n++;
  }
  ss.toast(`비어 있던 ${n}개 항목을 채웠어요.`, '쇼츠 현황판', 5);
}

/**
 * 수식으로 쓰고 싶을 때: =YT_META(C3) → 제목 · 채널 · 썸네일 URL 3칸
 * @param {string} url 유튜브 링크
 * @return {string[][]}
 * @customfunction
 */
function YT_META(url) {
  const m = fetchMeta(url);
  return m ? [[m.title, m.channel, m.thumbnail]] : [['', '', '']];
}

// ---------------------------------------------------------------------------
// 썸네일 업로드 (웹 앱). 로컬 앱(app.py) 이 JSON 으로 이미지를 보내면 드라이브에 저장하고 시트에 기록한다.
// 배포: 배포 › 새 배포 › 웹 앱 (실행 계정: 나, 액세스: 모든 사용자). 코드 수정 후에는 배포 관리 › 새 버전.
// 응답은 항상 HTTP 200 이고 본문 {ok, error?} 로 성공 여부를 알린다 (Apps Script 웹 앱은 상태 코드를 못 바꾼다).
// ---------------------------------------------------------------------------
function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** 배포 URL 확인용. 토큰 없이 호출해도 서비스 이름만 알려 준다. */
function doGet() {
  return jsonOut({ ok: true, service: 'shorts-thumbnail', version: UPLOAD_VERSION });
}

function doPost(e) {
  let body;
  try { body = JSON.parse(e && e.postData && e.postData.contents || ''); } catch (err) { return jsonOut({ ok: false, error: 'bad_json' }); }
  const token = uploadToken(false);
  if (!token || String(body.token || '') !== token) return jsonOut({ ok: false, error: 'unauthorized' });
  if (body.action === 'ping') return jsonOut({ ok: true, ping: true, version: UPLOAD_VERSION });
  if (['candidate_list', 'candidate_add', 'candidate_remove'].includes(body.action)) return candidateAction(body);
  if (String(body.action || '').startsWith('reference_')) return referenceAction(body);
  if (body.action !== 'thumbnail') return jsonOut({ ok: false, error: 'unknown_action' });
  const mime = String(body.mime || '');
  if (!/^image\/(jpeg|png|webp)$/.test(mime)) return jsonOut({ ok: false, error: 'bad_mime' });
  if (!body.data) return jsonOut({ ok: false, error: 'no_data' });

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return jsonOut({ ok: false, error: 'busy' });
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
    if (!sheet) return jsonOut({ ok: false, error: 'no_sheet' });
    const row = locateRow(sheet, Number(body.row), String(body.srcUrl || '').trim(), String(body.dish || '').trim());
    if (!row) return jsonOut({ ok: false, error: 'row_mismatch' });

    const folder = thumbFolder();
    const ext = mime === 'image/png' ? 'png' : mime === 'image/webp' ? 'webp' : 'jpg';
    const stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd-HHmmss');
    const safeDish = String(body.dish || '').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40);
    const name = `row${row}${safeDish ? '-' + safeDish : ''}-${stamp}.${ext}`;
    const file = folder.createFile(Utilities.newBlob(Utilities.base64Decode(String(body.data)), mime, name));
    file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
    const url = thumbUrlFor(file.getId());

    const old = String(sheet.getRange(row, COL.thumbUrl).getValue()).trim();
    sheet.getRange(row, COL.thumb).setFormula(`=IMAGE("${url}")`);
    sheet.getRange(row, COL.thumbUrl).setValue(url);
    touchRow(sheet, row, new Date());   // 스크립트가 고친 셀은 편집 트리거를 타지 않으므로 직접 찍는다
    trashOldThumb(old, folder);
    return jsonOut({ ok: true, row, url });
  } catch (err) {
    return jsonOut({ ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

function candidateVideoId(value) {
  const match = String(value || '').match(/(?:v=|\/shorts\/|youtu\.be\/|\/embed\/|\/live\/)([A-Za-z0-9_-]{11})/);
  return match ? match[1] : (/^[A-Za-z0-9_-]{11}$/.test(String(value || '')) ? String(value) : '');
}

function candidateRows(sheet) {
  const last = sheet.getLastRow();
  if (last < FIRST_DATA_ROW) return [];
  const values = sheet.getRange(FIRST_DATA_ROW, 1, last - FIRST_DATA_ROW + 1, LAST_COL).getValues();
  return values.map((r, i) => ({
    row: FIRST_DATA_ROW + i,
    // 새 후보는 참고 쇼츠 링크로 식별한다. 이전 배포에서 만든 후보는 원본 링크도 확인한다.
    videoId: candidateVideoId(r[COL.refUrls - 1]) || candidateVideoId(r[COL.srcUrl - 1]),
    status: stripEmoji(r[COL.status - 1]),
    label: String(r[COL.status - 1] || '').trim(),
  })).filter(x => x.videoId);
}

/** 문자열(여러 줄 링크 포함)에 들어 있는 유튜브 영상 ID 를 모두 뽑는다. */
function allVideoIds(text) {
  const ids = new Set();
  String(text || '').split(/\s+/).forEach(part => { const id = candidateVideoId(part); if (id) ids.add(id); });
  return [...ids];
}

/** 현황판 한 행이 가리키는 영상 ID 들 (참고 쇼츠 링크 + 원본 링크). */
function boardRowVideoIds(sheet, row) {
  const width = Math.max(COL.refUrls, COL.srcUrl);
  const vals = sheet.getRange(row, 1, 1, width).getValues()[0];
  return [...new Set([...allVideoIds(vals[COL.refUrls - 1]), ...allVideoIds(vals[COL.srcUrl - 1])])];
}

/** 현황판 상태 셀의 값을 현재 배치의 라벨로 정규화한다 ('후보'·'⭐ 후보' → '⭐ 촬영 후보'). 모르는 값은 그대로. */
function boardLabelOf(raw) {
  const key = stripEmoji(raw);
  if (!key) return '';
  const hit = STATUSES.find(st => stripEmoji(st.label) === key || st.legacy.includes(key));
  return hit ? hit.label : String(raw).trim();
}

/** 현황판 맨 위에 ⭐ 촬영 후보 행을 만든다. 레퍼 체크 웹과 레퍼 쇼츠 탭이 함께 쓴다. */
function boardAddCandidate(sheet, videoId, dishTitle, referenceChannel) {
  const row = nextCandidateRow(sheet);
  const now = new Date();
  sheet.getRange(row, COL.status).setValue(STATUSES[0].label);
  sheet.getRange(row, COL.dish).setValue(String(dishTitle || '').slice(0, 200));
  sheet.getRange(row, COL.refUrls).setValue(`https://www.youtube.com/shorts/${videoId}`);
  sheet.getRange(row, COL.refChannels).setValue(String(referenceChannel || '').slice(0, 200));
  sheet.getRange(row, COL.updatedAt).setValue(now);
  sheet.getRange(row, COL.createdAt).setValue(now);
  sheet.setRowHeight(row, ROW_HEIGHT);
  ensureBoardFilter(sheet);
  return row;
}

/** 후보는 필터를 적용해도 바로 보이도록 헤더 아래에 새 행을 만든다. */
function nextCandidateRow(sheet) {
  sheet.insertRowBefore(FIRST_DATA_ROW);
  const row = FIRST_DATA_ROW;
  const template = sheet.getRange(row + 1, 1, 1, LAST_COL);
  const target = sheet.getRange(row, 1, 1, LAST_COL);
  template.copyTo(target, SpreadsheetApp.CopyPasteType.PASTE_FORMAT, false);
  template.copyTo(target, SpreadsheetApp.CopyPasteType.PASTE_DATA_VALIDATION, false);
  return row;
}

function candidateAction(body) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (String(body.sheetId || '') !== ss.getId()) return jsonOut({ ok: false, error: 'wrong_sheet' });
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) return jsonOut({ ok: false, error: 'no_sheet' });
  if (body.action === 'candidate_list') {
    return jsonOut({ ok: true, version: UPLOAD_VERSION, items: candidateRows(sheet) });
  }
  const videoId = candidateVideoId(body.videoId || body.referenceUrl || body.url);
  if (!videoId) return jsonOut({ ok: false, error: 'bad_video' });
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return jsonOut({ ok: false, error: 'busy' });
  try {
    const existing = candidateRows(sheet).find(x => x.videoId === videoId);
    if (body.action === 'candidate_add') {
      if (existing) return jsonOut({ ok: true, item: existing, existing: true });
      const row = boardAddCandidate(sheet, videoId, body.dishTitle || body.title, body.referenceChannel || body.channel);
      refSyncBoardVideos(ss, [videoId]);
      return jsonOut({ ok: true, item: { row, videoId, status: CANDIDATE }, existing: false });
    }
    if (!existing) return jsonOut({ ok: true, removed: false });
    if (!isCandidateStatus(existing.status)) return jsonOut({ ok: false, error: 'not_candidate', item: existing });
    sheet.deleteRow(existing.row);
    refSyncBoardVideos(ss, [videoId]);
    return jsonOut({ ok: true, removed: true, videoId });
  } catch (err) {
    return jsonOut({ ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// 레퍼 체크 동기화 (웹 앱 액션 reference_*)
// ---------------------------------------------------------------------------

/** 레퍼 탭 두 개를 만들거나 새 배치로 맞춘다. 데이터는 옛 배치에서 읽어 그대로 옮긴다. */
function setupReferenceSheets(ss) {
  refSheet(ss, REF_CHANNELS_NAME, REF_CHANNEL_COLUMNS, REF_CHANNEL_LEGACY_HEADERS);
  refSheet(ss, REF_SHORTS_NAME, REF_SHORT_COLUMNS, REF_SHORT_LEGACY_HEADERS);
}

let refTzCache = null;
function refTz() {
  if (!refTzCache) refTzCache = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  return refTzCache;
}
const firstGlyph = (label) => Array.from(label)[0];   // 이모지(서로게이트 쌍) 안전한 첫 글자

function refSheet(ss, name, columns, legacy) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name, ss.getSheets().length);
  const n = columns.length;
  if (sheet.getMaxRows() < REF_FIRST_DATA_ROW) sheet.insertRowsAfter(sheet.getMaxRows(), REF_FIRST_DATA_ROW - sheet.getMaxRows());

  // 헤더가 현재 배치와 다르면(처음 만든 탭 제외) 옛 배치의 데이터를 읽어 두고 탭을 비운 뒤 새로 쓴다.
  const headerNow = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0].map(stripEmoji);
  const wanted = columns.map(c => stripEmoji(c.header));
  const sameLayout = wanted.every((h, i) => headerNow[i] === h);
  let carried = [];
  if (!sameLayout && sheet.getLastRow() >= REF_FIRST_DATA_ROW && headerNow.some(Boolean)) {
    carried = refReadByHeader(sheet, headerNow, columns, legacy || {});
    if (sheet.getFilter()) sheet.getFilter().remove();
    sheet.clear();
    sheet.clearConditionalFormatRules();
    sheet.getBandings().forEach(b => b.remove());
    sheet.showColumns(1, sheet.getMaxColumns());
  }
  if (sheet.getMaxColumns() < n) sheet.insertColumnsAfter(sheet.getMaxColumns(), n - sheet.getMaxColumns());
  else if (sheet.getMaxColumns() > n) sheet.deleteColumns(n + 1, sheet.getMaxColumns() - n);

  // 헤더
  const header = sheet.getRange(1, 1, 1, n);
  header.setValues([columns.map(c => c.header)]).setFontWeight('bold').setNumberFormat('@')
    .setBackground(REF_THEME.head_bg).setFontColor(REF_THEME.head_fg).setFontSize(11)
    .setVerticalAlignment('middle').setWrap(false);
  sheet.setRowHeight(1, 34);
  sheet.getRange(1, 1).setNote(REF_NOTE);

  // 데이터 영역 서식
  const dataRows = Math.max(sheet.getMaxRows() - 1, 1);
  const body = sheet.getRange(REF_FIRST_DATA_ROW, 1, dataRows, n);
  body.clearDataValidations();
  body.setVerticalAlignment('middle').setFontColor(PALETTE.text).setFontSize(10);
  sheet.showColumns(1, n);
  const rules = [];
  columns.forEach((c, i) => {
    sheet.setColumnWidth(i + 1, c.width);
    const rng = sheet.getRange(REF_FIRST_DATA_ROW, i + 1, dataRows, 1);
    // 텍스트 열은 @ 서식: 채널 ID 나 영상 ID 가 숫자/날짜로 바뀌지 않게 한다.
    rng.setNumberFormat(c.number ? '#,##0' : c.date ? 'yyyy-mm-dd hh:mm' : c.ymd ? 'yyyy-mm-dd' : c.image ? 'General' : '@');
    rng.setHorizontalAlignment(c.align || 'left').setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP);
    if (c.bold) rng.setFontWeight('bold');
    if (c.muted || c.date) rng.setFontColor(PALETTE.muted).setFontSize(9);
    if (c.badges) rng.setFontWeight('bold');
    if (c.badges) c.badges.forEach(b => rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenTextStartsWith(firstGlyph(b.label)).setBackground(b.bg).setFontColor(b.fg).setBold(true).setRanges([rng]).build()));
    if (c.board) rng.setDataValidation(SpreadsheetApp.newDataValidation()
      .requireValueInList(STATUSES.map(st => st.label), true).setAllowInvalid(true).setHelpText(REF_BOARD_HELP).build());
    if (c.hidden) sheet.hideColumns(i + 1);
  });
  sheet.setConditionalFormatRules(rules);
  sheet.getBandings().forEach(b => b.remove());
  body.applyRowBanding(SpreadsheetApp.BandingTheme.LIGHT_GREY, false, false)
    .setFirstRowColor('#ffffff').setSecondRowColor(PALETTE.band);
  body.setBorder(null, null, null, null, null, true, PALETTE.line, SpreadsheetApp.BorderStyle.SOLID);
  sheet.setRowHeightsForced(REF_FIRST_DATA_ROW, dataRows, REF_ROW_HEIGHT);
  // 헤더 필터: 조회수·좋아요·구독자 등 열 머리의 ▼ 버튼으로 정렬한다. 앱은 행 번호가 아니라 ID 로 행을 찾으므로 정렬해도 안전하다.
  if (sheet.getFilter()) sheet.getFilter().remove();
  sheet.getRange(1, 1, sheet.getMaxRows(), n).createFilter();
  if (sheet.getFrozenRows() < 1) sheet.setFrozenRows(1);
  if (carried.length) refWriteRows(sheet, columns, REF_FIRST_DATA_ROW, carried);
  return sheet;
}

/** 옛 배치의 탭을 헤더 이름으로 읽어 키→값 객체 목록으로 돌려준다 (레이아웃 변경 시 데이터 보존용). */
function refReadByHeader(sheet, headerNow, columns, legacy) {
  const keyOf = {};
  columns.forEach(c => { keyOf[stripEmoji(c.header)] = c; });
  const last = sheet.getLastRow();
  const range = sheet.getRange(REF_FIRST_DATA_ROW, 1, last - REF_FIRST_DATA_ROW + 1, headerNow.length);
  const values = range.getValues(), rich = range.getRichTextValues();
  return values.map((r, i) => {
    const item = {};
    headerNow.forEach((h, j) => {
      const col = keyOf[h];
      const key = col ? col.key : legacy[h];
      if (!key || (col && col.image)) return;
      item[key] = col ? refValue(r[j], col) : (r[j] === '' ? null : (r[j] instanceof Date ? refValue(r[j], { date: true }) : r[j]));
      if (col && col.link) { const url = rich[i][j] && rich[i][j].getLinkUrl(); if (url) item[col.link] = url; }
    });
    return item;
  }).filter(item => Object.values(item).some(v => v !== null && v !== '' && v !== false));
}

function refFlagLabel(complete, item) {
  if (complete) return REF_FLAGS[0].label;
  return item && item.shorts_updated_at ? REF_FLAGS[1].label : REF_FLAGS[2].label;
}

/** 로컬 앱의 값 → 셀 값. item 은 같은 행의 다른 값을 참고할 때(flag) 쓴다. */
function refCell(value, col, item) {
  if (col.flag) return refFlagLabel(value === true || value === 'TRUE' || (typeof value === 'string' && value.startsWith('✅')), item);
  if (col.detail) return (REF_DETAIL_STATUSES.find(d => d.key === value || d.label === value) || REF_DETAIL_STATUSES[1]).label;
  if (col.board) return value ? boardLabelOf(value) : '';
  if (col.bool) return value === true || value === 'TRUE' || value === 'true';
  if (col.number) return (value === null || value === undefined || value === '') ? '' : Number(value);
  if (col.date) return refToDate(value, "yyyy-MM-dd'T'HH:mm:ss", 19);
  if (col.ymd) return refToDate(value, 'yyyyMMdd', 8);
  if (value === null || value === undefined) return '';
  return String(value).slice(0, 49000);   // 셀 최대 50,000자
}

/** 셀 값 → 로컬 앱의 값. 날짜는 앱이 쓰는 형식(yyyy-MM-ddTHH:mm:ss · yyyyMMdd, 시트 시간대)의 문자열로 돌려준다. */
function refValue(value, col) {
  if (col.flag) return value === true || (typeof value === 'string' && value.startsWith('✅'));
  if (col.detail) {
    const raw = String(value || '').trim();
    const hit = REF_DETAIL_STATUSES.find(d => d.label === raw || d.key === raw || stripEmoji(d.label) === stripEmoji(raw));
    return hit ? hit.key : 'pending';
  }
  if (col.bool) return value === true || value === 'TRUE';
  if (col.number) return (value === '' || value === null) ? null : Number(value);
  if (value === '' || value === null || value === undefined) return null;
  if (value instanceof Date) return Utilities.formatDate(value, refTz(), col.ymd ? 'yyyyMMdd' : "yyyy-MM-dd'T'HH:mm:ss");
  return String(value);
}

function refToDate(value, pattern, length) {
  if (value instanceof Date) return value;
  const str = String(value === null || value === undefined ? '' : value).trim();
  if (!str) return '';
  try { return Utilities.parseDate(str.slice(0, length), refTz(), pattern); }
  catch (err) { return str; }   // 형식이 다르면 문자열 그대로 둔다
}

/**
 * 탭의 데이터 행을 {row, item} 목록으로 읽는다. keys 를 주면 그 키들이 놓인 열 구간만 읽는다(키 조회용).
 * 링크 열은 URL 도 함께 읽고, URL 이 없으면 ID 로 복원한다.
 */
function refRows(sheet, columns, keys) {
  const last = sheet.getLastRow();
  if (last < REF_FIRST_DATA_ROW) return [];
  let first = 0, cols = columns;
  if (keys) {
    const idx = keys.map(k => columns.findIndex(c => c.key === k));
    first = Math.min(...idx);
    cols = columns.slice(first, Math.max(...idx) + 1);
  }
  const count = last - REF_FIRST_DATA_ROW + 1;
  const values = sheet.getRange(REF_FIRST_DATA_ROW, first + 1, count, cols.length).getValues();
  const links = {};
  cols.forEach((c, j) => {
    if (c.link) links[j] = sheet.getRange(REF_FIRST_DATA_ROW, first + j + 1, count, 1).getRichTextValues().map(r => r[0] ? r[0].getLinkUrl() : null);
  });
  return values.map((r, i) => ({ row: REF_FIRST_DATA_ROW + i, item: refItemFrom(r, cols, links, i) }));
}

function refItemFrom(values, cols, links, i) {
  const item = {};
  cols.forEach((c, j) => {
    if (c.image) return;
    item[c.key] = refValue(values[j], c);
    if (c.link) item[c.link] = (links[j] && links[j][i]) || null;
  });
  if (cols.some(c => c.link === 'url') && !item.url) {
    if (item.id) item.url = `https://www.youtube.com/channel/${item.id}`;
    else if (item.video_id) item.url = `https://www.youtube.com/shorts/${item.video_id}`;
  }
  return item;
}

/** 한 행만 읽는다 (편집 트리거용). */
function refRowItem(sheet, columns, row) {
  const values = sheet.getRange(row, 1, 1, columns.length).getValues()[0];
  const links = {};
  columns.forEach((c, j) => { if (c.link) { const rv = sheet.getRange(row, j + 1).getRichTextValue(); links[j] = [rv ? rv.getLinkUrl() : null]; } });
  return refItemFrom(values, columns, links, 0);
}

function refDeleteRows(sheet, columns, keys, keep) {
  const rows = refRows(sheet, columns, keys).filter(x => !keep(x.item)).map(x => x.row);
  rows.reverse().forEach(r => sheet.deleteRow(r));   // 아래에서 위로 지워야 행 번호가 밀리지 않는다
  return rows.length;
}

/** items(키→값 객체)를 startRow 부터 한 행씩 쓴다. 이미지 열은 IMAGE() 수식, 링크 열은 텍스트+링크로 넣는다. */
function refWriteRows(sheet, columns, startRow, items) {
  if (!items.length) return;
  const need = startRow + items.length - 1 - sheet.getMaxRows();
  if (need > 0) sheet.insertRowsAfter(sheet.getMaxRows(), need);
  const matrix = items.map(it => columns.map(c => (c.image || c.link) ? '' : refCell(it[c.key], c, it)));
  sheet.getRange(startRow, 1, items.length, columns.length).setValues(matrix);
  columns.forEach((c, j) => {
    const rng = sheet.getRange(startRow, j + 1, items.length, 1);
    if (c.image) {
      const src = colLetter(columns.findIndex(x => x.key === c.image) + 1);
      rng.setFormulas(items.map((_, i) => [`=IF(${src}${startRow + i}="","",IMAGE(${src}${startRow + i}))`]));
    }
    if (c.link) {
      rng.setRichTextValues(items.map(it => {
        const text = String(it[c.key] || ''), url = String(it[c.link] || '');
        const b = SpreadsheetApp.newRichTextValue().setText(text);
        if (text && /^https?:\/\//.test(url)) b.setLinkUrl(url);
        return [b.build()];
      }));
    }
  });
}

function refAppendRows(sheet, columns, items) {
  refWriteRows(sheet, columns, sheet.getLastRow() + 1, items);
}

// ---- 현황판 상태 ↔ 레퍼 쇼츠 '📌 현황판' 열 ----

/** 현황판에 있는 영상 ID → 상태 라벨. 같은 영상이 여러 행이면 위쪽 행을 쓴다. */
function refBoardMap(ss) {
  const board = ss.getSheetByName(SHEET_NAME);
  const map = new Map();
  if (!board) return map;
  candidateRows(board).forEach(x => { if (!map.has(x.videoId)) map.set(x.videoId, boardLabelOf(x.label)); });
  return map;
}

/** 주어진 영상들의 현황판 상태를 레퍼 쇼츠 탭에 다시 써 준다 (현황판 편집·후보 등록/해제 직후). */
function refSyncBoardVideos(ss, videoIds) {
  const shorts = ss.getSheetByName(REF_SHORTS_NAME);
  if (!shorts || !videoIds.length) return 0;
  const map = refBoardMap(ss);
  const boardCol = REF_SHORT_COLUMNS.findIndex(c => c.board) + 1;
  const wanted = new Set(videoIds);
  let changed = 0;
  refRows(shorts, REF_SHORT_COLUMNS, ['board_status', 'video_id']).forEach(x => {
    if (!wanted.has(x.item.video_id)) return;
    const label = map.get(x.item.video_id) || '';
    if ((x.item.board_status || '') !== label) { shorts.getRange(x.row, boardCol).setValue(label); changed++; }
  });
  return changed;
}

/** 한 채널의 레퍼 쇼츠 행 전체를 현황판과 맞춘다 (행 삭제처럼 트리거가 못 잡는 변경까지 따라잡는다). */
function refReconcileBoard(ss, shorts, rows) {
  const map = refBoardMap(ss);
  const boardCol = REF_SHORT_COLUMNS.findIndex(c => c.board) + 1;
  let changed = 0;
  rows.forEach(x => {
    const label = map.get(x.item.video_id) || '';
    if ((x.item.board_status || '') !== label) { shorts.getRange(x.row, boardCol).setValue(label); x.item.board_status = label; changed++; }
  });
  return changed;
}

function refChannelName(ss, channelId) {
  const channels = ss.getSheetByName(REF_CHANNELS_NAME);
  if (!channels || !channelId) return '';
  const hit = refRows(channels, REF_CHANNEL_COLUMNS).find(x => x.item.id === channelId);
  return hit ? (hit.item.name || '') : '';
}

/** 로컬 앱의 쇼츠 항목(pinned_comment 객체 포함) ↔ 시트 한 행(키→값). */
function shortToFlat(channelId, item, syncedAt, boardMap) {
  const pinned = item.pinned_comment && typeof item.pinned_comment === 'object' ? item.pinned_comment : {};
  return { ...item, channel_id: channelId, video_id: item.id,
           pinned_comment_text: pinned.text, pinned_comment_author: pinned.author, synced_at: syncedAt,
           board_status: (boardMap && boardMap.get(item.id)) || '' };
}

function rowToShort(flat) {
  const item = { id: flat.video_id, title: flat.title, description: flat.description, url: flat.url, thumbnail: flat.thumbnail,
                 view_count: flat.view_count, like_count: flat.like_count, comment_count: flat.comment_count,
                 upload_date: flat.upload_date, detail_status: flat.detail_status || 'pending',
                 pinned_comment: flat.pinned_comment_text ? { text: flat.pinned_comment_text, author: flat.pinned_comment_author } : null };
  if (flat.detail_error) item.detail_error = flat.detail_error;
  return item;
}

/** 채널 행(키→값) → 로컬 앱의 채널 객체. 화면용 열(thumb)은 빼고 앱이 쓰는 키만 돌려준다. */
function rowToChannel(flat) {
  return { id: flat.id, name: flat.name, url: flat.url, thumbnail: flat.thumbnail, description: flat.description,
           subscriber_count: flat.subscriber_count, added_at: flat.added_at,
           shorts_complete: !!flat.shorts_complete, shorts_updated_at: flat.shorts_updated_at || null };
}

function referenceAction(body) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (String(body.sheetId || '') !== ss.getId()) return jsonOut({ ok: false, error: 'wrong_sheet' });
  const channels = ss.getSheetByName(REF_CHANNELS_NAME) || refSheet(ss, REF_CHANNELS_NAME, REF_CHANNEL_COLUMNS, REF_CHANNEL_LEGACY_HEADERS);
  const shorts = ss.getSheetByName(REF_SHORTS_NAME) || refSheet(ss, REF_SHORTS_NAME, REF_SHORT_COLUMNS, REF_SHORT_LEGACY_HEADERS);
  const channelId = String(body.channelId || body.id || '');
  const channelRows = () => refRows(channels, REF_CHANNEL_COLUMNS).filter(x => x.item.id);

  if (body.action === 'reference_list') {
    return jsonOut({ ok: true, version: UPLOAD_VERSION, items: channelRows().map(x => rowToChannel(x.item)) });
  }
  if (body.action === 'reference_shorts_get') {
    if (!channelId) return jsonOut({ ok: false, error: 'bad_channel' });
    const channel = channelRows().find(x => x.item.id === channelId);
    const rows = refRows(shorts, REF_SHORT_COLUMNS).filter(x => x.item.channel_id === channelId && x.item.video_id);
    refReconcileBoard(ss, shorts, rows);   // 현황판에서 지워진 행 등 트리거가 못 잡은 변경을 따라잡는다
    const items = rows.map(x => rowToShort(x.item));
    return jsonOut({ ok: true, version: UPLOAD_VERSION, items,
                     complete: !!(channel && channel.item.shorts_complete),
                     updated_at: channel ? channel.item.shorts_updated_at : null });
  }
  if (!['reference_add', 'reference_remove', 'reference_shorts_put'].includes(body.action)) return jsonOut({ ok: false, error: 'unknown_action' });

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return jsonOut({ ok: false, error: 'busy' });
  try {
    if (body.action === 'reference_add') {
      const channel = body.channel && typeof body.channel === 'object' ? body.channel : null;
      if (!channel || !channel.id) return jsonOut({ ok: false, error: 'bad_channel' });
      const existing = channelRows().find(x => x.item.id === String(channel.id));
      if (existing) return jsonOut({ ok: true, item: rowToChannel(existing.item), existing: true });
      refAppendRows(channels, REF_CHANNEL_COLUMNS, [channel]);
      return jsonOut({ ok: true, item: channel, existing: false });
    }
    if (body.action === 'reference_remove') {
      if (!channelId) return jsonOut({ ok: false, error: 'bad_channel' });
      const removed = refDeleteRows(channels, REF_CHANNEL_COLUMNS, null, c => c.id !== channelId);
      const shortsRemoved = refDeleteRows(shorts, REF_SHORT_COLUMNS, ['channel_id'], v => v.channel_id !== channelId);
      return jsonOut({ ok: true, removed: removed > 0, shortsRemoved });
    }
    // reference_shorts_put: (채널 ID, 영상 ID) 로 갱신하거나 새 행을 붙인다. 채널 행의 수집 상태·저장 시각도 갱신.
    if (!channelId) return jsonOut({ ok: false, error: 'bad_channel' });
    const items = Array.isArray(body.items) ? body.items.filter(v => v && typeof v === 'object' && v.id) : [];
    const syncedAt = Utilities.formatDate(new Date(), refTz(), "yyyy-MM-dd'T'HH:mm:ss");
    const boardMap = refBoardMap(ss);
    const rowOf = new Map(refRows(shorts, REF_SHORT_COLUMNS, ['channel_id', 'video_id']).map(x => [`${x.item.channel_id}\n${x.item.video_id}`, x.row]));
    let updated = 0;
    const fresh = [];
    items.forEach(item => {
      const flat = shortToFlat(channelId, item, syncedAt, boardMap);
      const at = rowOf.get(`${channelId}\n${item.id}`);
      if (at) { refWriteRows(shorts, REF_SHORT_COLUMNS, at, [flat]); updated++; }
      else fresh.push(flat);
    });
    refAppendRows(shorts, REF_SHORT_COLUMNS, fresh);
    const channel = channelRows().find(x => x.item.id === channelId);
    if (channel && (body.complete !== undefined || body.updated_at)) {
      const flat = { ...channel.item };
      if (body.updated_at) flat.shorts_updated_at = String(body.updated_at);
      if (body.complete !== undefined) flat.shorts_complete = !!body.complete;
      REF_CHANNEL_COLUMNS.forEach((c, j) => {
        if (c.key === 'shorts_complete' || c.key === 'shorts_updated_at') channels.getRange(channel.row, j + 1).setValue(refCell(flat[c.key], c, flat));
      });
    }
    return jsonOut({ ok: true, updated, inserted: fresh.length, channelFound: !!channel });
  } catch (err) {
    return jsonOut({ ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

/**
 * 웹 화면이 보낸 행 번호가 아직 같은 항목을 가리키는지 확인한다 (사이에 행이 끼거나 지워졌을 수 있다).
 * 원본 링크(없으면 요리 제목)가 일치하면 그 행. 아니면 같은 키를 가진 행을 찾아 정확히 하나일 때만 그 행. 못 찾으면 0.
 */
function locateRow(sheet, row, srcUrl, dish) {
  const last = sheet.getLastRow();
  if (last < FIRST_DATA_ROW) return 0;
  const matches = (vals) => srcUrl ? String(vals[COL.srcUrl - 1]).trim() === srcUrl
                                   : (dish ? String(vals[COL.dish - 1]).trim() === dish : false);
  const width = Math.max(COL.srcUrl, COL.dish);
  if (!srcUrl && !dish) return (row >= FIRST_DATA_ROW && row <= last) ? row : 0;   // 확인할 키가 없으면 행 번호를 믿는다
  if (row >= FIRST_DATA_ROW && row <= last && matches(sheet.getRange(row, 1, 1, width).getValues()[0])) return row;
  const hits = sheet.getRange(FIRST_DATA_ROW, 1, last - FIRST_DATA_ROW + 1, width).getValues()
    .map((vals, i) => matches(vals) ? FIRST_DATA_ROW + i : 0).filter(Boolean);
  return hits.length === 1 ? hits[0] : 0;
}

/** 썸네일 보관 폴더. 처음 한 번 만들고 ID 를 스크립트 속성에 기억한다. */
function thumbFolder() {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty(PROP_FOLDER);
  if (id) {
    try { const f = DriveApp.getFolderById(id); if (!f.isTrashed()) return f; } catch (err) { /* 지워졌으면 새로 만든다 */ }
  }
  const folder = DriveApp.createFolder(THUMB_FOLDER_NAME);
  props.setProperty(PROP_FOLDER, folder.getId());
  return folder;
}

/** 이전 썸네일이 우리 폴더의 파일이면 휴지통으로 보낸다. 다른 곳의 이미지는 건드리지 않는다. */
function trashOldThumb(url, folder) {
  const m = /googleusercontent\.com\/d\/([\w-]+)|[?&]id=([\w-]+)/.exec(url || '');
  if (!m) return;
  try {
    const file = DriveApp.getFileById(m[1] || m[2]);
    const parents = file.getParents();
    while (parents.hasNext()) {
      if (parents.next().getId() === folder.getId()) { file.setTrashed(true); return; }
    }
  } catch (err) { /* 이미 지워진 파일 */ }
}

function uploadToken(create) {
  const props = PropertiesService.getScriptProperties();
  let token = props.getProperty(PROP_TOKEN);
  if (!token && create) {
    token = (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, '');
    props.setProperty(PROP_TOKEN, token);
  }
  return token;
}

/** 메뉴: 웹 앱 URL 과 토큰을 보여 준다. 토큰이 없으면 만든다. */
function showUploadInfo() {
  const url = ScriptApp.getService().getUrl() || '';
  const esc = (s) => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const html = HtmlService.createHtmlOutput(
    '<style>body{font:13px -apple-system,sans-serif;padding:4px 8px}p{margin:10px 0 4px;color:#475569}input{width:100%;box-sizing:border-box;font:12px ui-monospace,monospace;padding:6px}</style>' +
    '<p>웹 앱 URL</p>' +
    (url ? `<input readonly value="${esc(url)}" onclick="this.select()">`
         : '<input readonly value="아직 웹 앱으로 배포되지 않았습니다. Apps Script 편집기에서 배포 › 새 배포 › 웹 앱을 진행하세요.">') +
    '<p>업로드 토큰</p>' +
    `<input readonly value="${esc(uploadToken(true))}" onclick="this.select()">` +
    '<p>로컬 앱의 쇼츠 현황판(/shorts) 화면 › 썸네일 업로드 설정에 두 값을 붙여 넣으세요.</p>'
  ).setWidth(560).setHeight(250);
  SpreadsheetApp.getUi().showModalDialog(html, '썸네일 업로드 연결 정보');
}

/** 메뉴: 토큰을 새로 만든다. 로컬 앱에도 다시 입력해야 한다. */
function regenerateUploadToken() {
  const ui = SpreadsheetApp.getUi();
  const answer = ui.alert('업로드 토큰 다시 만들기', '기존 토큰은 바로 무효가 되고, 로컬 앱에 새 토큰을 다시 입력해야 합니다. 계속할까요?', ui.ButtonSet.YES_NO);
  if (answer !== ui.Button.YES) return;
  PropertiesService.getScriptProperties().deleteProperty(PROP_TOKEN);
  showUploadInfo();
}
