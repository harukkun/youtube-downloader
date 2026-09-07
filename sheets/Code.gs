/**
 * 쇼츠 현황판 — Google Sheets + Apps Script
 *
 * 사용법 (한 번만):
 *   1) 새 스프레드시트 → 확장 프로그램 › Apps Script → 이 파일 내용을 Code.gs 에 붙여넣고 저장
 *   2) 편집기 상단 함수 선택에서 setupSheets 를 고르고 ▶ 실행 → 권한 승인
 *   3) 시트로 돌아오면 '현황판'·'요약' 시트와 '쇼츠 현황판' 메뉴가 생긴다
 *
 * 자동화:
 *   - 원본 링크 열에 유튜브 링크를 붙이면 원본 제목·채널·썸네일이 채워진다
 *   - 참고 쇼츠 링크 열(여러 개는 줄바꿈)을 채우면 참고 채널이 줄마다 채워진다
 *   - 상태가 '✅ 업로드 완료'가 아닌 행에서는 플랫폼 체크·링크 입력이 되돌려진다
 *   - 행을 고치면 수정일, 요리 제목을 처음 넣으면 등록일이 찍힌다
 *
 * setupSheets 는 다시 실행해도 안전하다. 이전 버전 배치(헤더가 1행, 설명 글자수 열, 이모지 없는 상태값)는
 * 자동으로 새 배치로 옮긴다. 데이터는 지우지 않는다.
 *
 * Apps Script 는 자바스크립트(V8)다. UrlFetchApp/SpreadsheetApp 만 구글 제공 객체.
 */

const SHEET_NAME = '현황판';
const SUMMARY_NAME = '요약';
const GROUP_ROW = 1;        // 열 그룹 띠 (📋 기본 · 🎬 원본 영상 …)
const HEADER_ROW = 2;       // 열 이름
const FIRST_DATA_ROW = 3;
const MIN_ROWS = 1000;
const ROW_HEIGHT = 64;      // 썸네일이 보이는 데이터 행 높이

const STATUSES = [
  { label: '⬜ 제작 전',     legacy: '제작 전',             bg: '#f1f5f9', fg: '#475569' },
  { label: '🎬 제작 중',     legacy: '제작 중',             bg: '#dbeafe', fg: '#1d4ed8' },
  { label: '⏳ 업로드 대기', legacy: '제작 완료·업로드 대기', bg: '#fef3c7', fg: '#b45309' },
  { label: '✅ 업로드 완료', legacy: '업로드 완료',          bg: '#dcfce7', fg: '#15803d' },
];
const UPLOADED = STATUSES[STATUSES.length - 1].label;

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
  { key: 'srcUrl',      header: '🔗 원본 링크',    width: 200, group: 'source', note: '유튜브 링크를 붙이면 원본 제목·채널·썸네일이 자동으로 채워집니다.' },
  { key: 'srcTitle',    header: '🎞️ 원본 제목',    width: 240, group: 'source', wrap: true },
  { key: 'srcChannel',  header: '📺 원본 채널',    width: 120, group: 'source' },
  { key: 'thumb',       header: '🖼️ 썸네일',       width: 110, group: 'source' },
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
const stripEmoji = (s) => String(s || '').replace(/^[^\p{L}\p{N}]+/u, '').trim();   // '📌 상태' → '상태'

// ---------------------------------------------------------------------------
// 메뉴
// ---------------------------------------------------------------------------
function onOpen() {
  SpreadsheetApp.getUi().createMenu('쇼츠 현황판')
    .addItem('🎨 시트 초기 설정 (다시 실행해도 안전)', 'setupSheets')
    .addSeparator()
    .addItem('🔄 선택한 행 정보 다시 가져오기', 'fillSelectedRows')
    .addItem('✨ 빈 정보 모두 채우기', 'fillAllMissing')
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
  installEditTrigger(ss);
  ss.setActiveSheet(sheet);
  ss.toast('설정 완료. 원본 링크를 붙이면 제목·채널·썸네일이 자동으로 채워집니다.', '쇼츠 현황판', 8);
}

/** 이전 버전 배치를 새 배치로 옮긴다. 데이터는 지우지 않는다. */
function migrateLayout(sheet) {
  const lastCol = Math.max(sheet.getLastColumn(), 1);
  // v1: 열 이름이 1행에 있었고 그룹 띠가 없었다 → 위에 한 행 삽입
  const a1 = stripEmoji(sheet.getRange(1, 1).getValue());
  if (a1 === stripEmoji(COLUMNS[0].header)) sheet.insertRowBefore(1);

  // 지운 열 제거 (헤더 이름으로 찾음)
  const hdr = sheet.getRange(HEADER_ROW, 1, 1, lastCol).getValues()[0].map(stripEmoji);
  for (let i = hdr.length - 1; i >= 0; i--) {
    if (REMOVED_HEADERS.includes(hdr[i])) sheet.deleteColumn(i + 1);
  }

  // 상태값 라벨 변경 (이모지 없는 예전 라벨 → 새 라벨)
  const lastRow = sheet.getLastRow();
  if (lastRow >= FIRST_DATA_ROW) {
    const rng = sheet.getRange(FIRST_DATA_ROW, COL.status, lastRow - FIRST_DATA_ROW + 1, 1);
    const vals = rng.getValues();
    let changed = false;
    vals.forEach(r => {
      const hit = STATUSES.find(s => s.legacy === String(r[0]).trim());
      if (hit) { r[0] = hit.label; changed = true; }
    });
    if (changed) rng.setValues(vals);
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
  });
  sheet.getRange(HEADER_ROW, 1, 1, LAST_COL)
    .setBorder(null, null, true, null, null, null, PALETTE.line, SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  sheet.setRowHeight(HEADER_ROW, 36);
  sheet.setFrozenRows(HEADER_ROW);
  sheet.setFrozenColumns(2);   // 상태 + 요리 제목 고정

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
  if (sheet.getName() !== SHEET_NAME) return;
  const top = e.range.getRow(), left = e.range.getColumn();
  const numRows = e.range.getNumRows(), numCols = e.range.getNumColumns();
  if (top + numRows - 1 < FIRST_DATA_ROW) return;   // 헤더만 편집
  const single = numRows === 1 && numCols === 1;
  const now = new Date();

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
      touched = true;
    }
    if (touched) touchRow(sheet, r, now);
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

/** 원본 링크 → 원본 제목·채널·썸네일. force=false 면 비어 있는 칸만 채운다. 채웠으면 true. */
function fillSource(sheet, row, force) {
  const url = String(sheet.getRange(row, COL.srcUrl).getValue()).trim();
  const targets = sheet.getRange(row, COL.srcTitle, 1, 3);   // srcTitle, srcChannel, thumb (연속 3열)
  if (!url) { targets.clearContent(); return false; }
  if (!force && targets.getValues()[0].every(v => String(v).trim() !== '')) return false;
  const m = fetchMeta(url);
  if (!m) {
    SpreadsheetApp.getActiveSpreadsheet().toast(`원본 정보를 가져올 수 없어요. 링크를 확인해 주세요. (${row}행)`, '쇼츠 현황판', 5);
    return false;
  }
  sheet.getRange(row, COL.srcTitle).setValue(m.title);
  sheet.getRange(row, COL.srcChannel).setValue(m.channel);
  const thumb = sheet.getRange(row, COL.thumb);
  if (m.thumbnail) thumb.setFormula(`=IMAGE("${m.thumbnail}")`); else thumb.clearContent();
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
