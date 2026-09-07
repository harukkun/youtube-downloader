/**
 * 쇼츠 현황판 — Google Sheets + Apps Script
 *
 * 사용법 (한 번만):
 *   1) 새 스프레드시트 → 확장 프로그램 › Apps Script → 이 파일 내용을 Code.gs 에 붙여넣고 저장
 *   2) 편집기 상단 함수 선택에서 setupSheets 를 고르고 ▶ 실행 → 권한 승인
 *   3) 시트로 돌아오면 '현황판'·'요약' 시트와 '쇼츠 현황판' 메뉴가 생긴다
 *
 * 자동화:
 *   - C열(원본 링크)에 유튜브 링크를 붙이면 D(제목)·E(채널)·F(썸네일)가 채워진다
 *   - G열(참고 쇼츠 링크, 여러 개는 줄바꿈)을 채우면 H(참고 채널)가 줄마다 채워진다
 *   - 상태가 '업로드 완료'가 아닌 행에서는 플랫폼 체크·링크 입력이 되돌려진다
 *   - 행을 고치면 수정일, 요리 제목을 처음 넣으면 등록일이 찍힌다
 *
 * Apps Script 는 자바스크립트(V8)다. UrlFetchApp/SpreadsheetApp 만 구글 제공 객체.
 */

const SHEET_NAME = '현황판';
const SUMMARY_NAME = '요약';
const FIRST_DATA_ROW = 2;
const MIN_ROWS = 1000;
const ROW_HEIGHT = 64;      // 썸네일이 보이는 데이터 행 높이
const UPLOADED = '업로드 완료';

const STATUSES = [
  { label: '제작 전',             bg: '#e8eaed', fg: '#5f6368' },
  { label: '제작 중',             bg: '#e8f0fe', fg: '#1a73e8' },
  { label: '제작 완료·업로드 대기', bg: '#fef7e0', fg: '#b06000' },
  { label: UPLOADED,             bg: '#e6f4ea', fg: '#137333' },
];
const PLATFORMS = [
  { key: 'youtube',   label: '유튜브' },
  { key: 'instagram', label: '인스타그램' },
  { key: 'tiktok',    label: '틱톡' },
  { key: 'naverClip', label: '네이버 클립' },
];

// 열 정의. 배열 순서가 곧 열 순서(A, B, C …)다. 열을 추가/이동할 때 여기만 고치면 된다.
const COLUMNS = [
  { key: 'status',      header: '상태',          width: 170 },
  { key: 'dish',        header: '요리 제목',      width: 180, wrap: true },
  { key: 'srcUrl',      header: '원본 링크',      width: 200, note: '유튜브 링크를 붙이면 원본 제목·채널·썸네일이 자동으로 채워집니다.' },
  { key: 'srcTitle',    header: '원본 제목',      width: 240, wrap: true },
  { key: 'srcChannel',  header: '원본 채널',      width: 120 },
  { key: 'thumb',       header: '썸네일',         width: 110 },
  { key: 'refUrls',     header: '참고 쇼츠 링크',  width: 220, wrap: true, note: '여러 개는 줄바꿈(⌥⏎ / Alt+Enter)으로 한 줄에 하나씩. 참고 채널이 같은 순서로 채워집니다.' },
  { key: 'refChannels', header: '참고 채널',      width: 120, wrap: true },
  ...PLATFORMS.flatMap(p => [
    { key: p.key + 'On',  header: p.label + ' ☑',  width: 72,  checkbox: true, platform: true },
    { key: p.key + 'Url', header: p.label + ' 링크', width: 160, platform: true },
  ]),
  { key: 'title',       header: '영상 제목',      width: 240, wrap: true },
  { key: 'titleLen',    header: '제목 글자수',    width: 80,  lenOf: 'title', limit: 100 },
  { key: 'desc',        header: '설명',           width: 320, wrap: true },
  { key: 'descLen',     header: '설명 글자수',    width: 80,  lenOf: 'desc',  limit: 5000 },
  { key: 'pinned',      header: '고정 댓글',      width: 240, wrap: true },
  { key: 'memo',        header: '메모',           width: 200, wrap: true },
  { key: 'updatedAt',   header: '수정일',         width: 130, date: true },
  { key: 'createdAt',   header: '등록일',         width: 130, date: true },
];
const COL = Object.fromEntries(COLUMNS.map((c, i) => [c.key, i + 1]));  // key → 1부터 시작하는 열 번호
const LAST_COL = COLUMNS.length;
const PLATFORM_FIRST_COL = COL[PLATFORMS[0].key + 'On'];
const PLATFORM_LAST_COL = COL[PLATFORMS[PLATFORMS.length - 1].key + 'Url'];

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
    .addItem('시트 초기 설정 (다시 실행해도 안전)', 'setupSheets')
    .addSeparator()
    .addItem('선택한 행 정보 다시 가져오기', 'fillSelectedRows')
    .addItem('빈 정보 모두 채우기', 'fillAllMissing')
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
  setupBoardSheet(sheet);
  setupSummarySheet(ss);
  installEditTrigger(ss);
  ss.setActiveSheet(sheet);
  ss.toast('설정 완료. C열에 원본 링크를 붙이면 제목·채널·썸네일이 자동으로 채워집니다.', '쇼츠 현황판', 8);
}

function setupBoardSheet(sheet) {
  // 열·행 개수 확보
  if (sheet.getMaxColumns() < LAST_COL) sheet.insertColumnsAfter(sheet.getMaxColumns(), LAST_COL - sheet.getMaxColumns());
  if (sheet.getMaxRows() < MIN_ROWS) sheet.insertRowsAfter(sheet.getMaxRows(), MIN_ROWS - sheet.getMaxRows());
  const maxRows = sheet.getMaxRows();
  const dataRows = maxRows - FIRST_DATA_ROW + 1;

  // 헤더 (항상 다시 씀. 데이터 행은 건드리지 않음)
  const header = sheet.getRange(1, 1, 1, LAST_COL);
  header.setValues([COLUMNS.map(c => c.header)])
    .setFontWeight('bold').setBackground('#202124').setFontColor('#ffffff')
    .setVerticalAlignment('middle').setWrap(false);
  sheet.setRowHeight(1, 34);
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(2);   // 상태 + 요리 제목 고정
  COLUMNS.forEach((c, i) => {
    sheet.setColumnWidth(i + 1, c.width);
    const cell = sheet.getRange(1, i + 1);
    if (c.note) cell.setNote(c.note); else cell.clearNote();
  });

  // 데이터 영역 공통 서식
  const body = sheet.getRange(FIRST_DATA_ROW, 1, dataRows, LAST_COL);
  body.setVerticalAlignment('middle');
  COLUMNS.forEach((c, i) => {
    const rng = sheet.getRange(FIRST_DATA_ROW, i + 1, dataRows, 1);
    rng.setWrapStrategy(c.wrap ? SpreadsheetApp.WrapStrategy.WRAP : SpreadsheetApp.WrapStrategy.CLIP);
    if (c.date) rng.setNumberFormat('yyyy-mm-dd hh:mm');
    if (c.lenOf) rng.setHorizontalAlignment('center').setFontColor('#5f6368');
    if (c.checkbox) rng.setHorizontalAlignment('center');
  });
  sheet.setRowHeightsForced(FIRST_DATA_ROW, dataRows, ROW_HEIGHT);

  // 데이터 확인: 상태 드롭다운, 플랫폼 체크박스
  const statusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(STATUSES.map(s => s.label), true).setAllowInvalid(false)
    .setHelpText('제작 전 → 제작 중 → 제작 완료·업로드 대기 → 업로드 완료').build();
  sheet.getRange(FIRST_DATA_ROW, COL.status, dataRows, 1).setDataValidation(statusRule);
  const checkboxRule = SpreadsheetApp.newDataValidation().requireCheckbox().build();
  COLUMNS.forEach((c, i) => { if (c.checkbox) sheet.getRange(FIRST_DATA_ROW, i + 1, dataRows, 1).setDataValidation(checkboxRule); });

  // 글자수 열: 2행에 ARRAYFORMULA 하나로 전체 계산 (아래 셀은 비워 둬야 하므로 정리)
  COLUMNS.forEach((c, i) => {
    if (!c.lenOf) return;
    const src = colLetter(COL[c.lenOf]);
    if (dataRows > 1) sheet.getRange(FIRST_DATA_ROW + 1, i + 1, dataRows - 1, 1).clearContent();
    sheet.getRange(FIRST_DATA_ROW, i + 1)
      .setFormula(`=ARRAYFORMULA(IF(${src}${FIRST_DATA_ROW}:${src}="","",LEN(${src}${FIRST_DATA_ROW}:${src})))`);
  });

  // 조건부 서식 (이 시트의 규칙을 통째로 다시 만든다)
  const rules = [];
  const statusRange = sheet.getRange(FIRST_DATA_ROW, COL.status, dataRows, 1);
  STATUSES.forEach(s => {
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo(s.label).setBackground(s.bg).setFontColor(s.fg).setBold(true)
      .setRanges([statusRange]).build());
  });
  const platRange = sheet.getRange(FIRST_DATA_ROW, PLATFORM_FIRST_COL, dataRows, PLATFORM_LAST_COL - PLATFORM_FIRST_COL + 1);
  rules.push(SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied(`=$${colLetter(COL.status)}${FIRST_DATA_ROW}<>"${UPLOADED}"`)
    .setBackground('#f1f3f4').setFontColor('#9aa0a6')
    .setRanges([platRange]).build());
  COLUMNS.forEach((c, i) => {
    if (!c.limit) return;
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenNumberGreaterThan(c.limit).setBackground('#fce8e6').setFontColor('#c5221f').setBold(true)
      .setRanges([sheet.getRange(FIRST_DATA_ROW, i + 1, dataRows, 1)]).build());
  });
  sheet.setConditionalFormatRules(rules);
}

function setupSummarySheet(ss) {
  let sheet = ss.getSheetByName(SUMMARY_NAME);
  if (!sheet) sheet = ss.insertSheet(SUMMARY_NAME, 1);
  sheet.clear();
  const b = `'${SHEET_NAME}'`;
  const st = colLetter(COL.status), dish = colLetter(COL.dish);
  const rows = [['상태별', '개수']];
  STATUSES.forEach(s => rows.push([s.label, `=COUNTIF(${b}!$${st}$${FIRST_DATA_ROW}:$${st}, A${rows.length + 1})`]));
  rows.push(['전체 항목', `=COUNTA(${b}!$${dish}$${FIRST_DATA_ROW}:$${dish})`]);
  rows.push(['', '']);
  rows.push(['플랫폼별 업로드', '개수']);
  PLATFORMS.forEach(p => {
    const c = colLetter(COL[p.key + 'On']);
    rows.push([p.label, `=COUNTIF(${b}!$${c}$${FIRST_DATA_ROW}:$${c}, TRUE)`]);
  });
  sheet.getRange(1, 1, rows.length, 2).setValues(rows);
  [1, STATUSES.length + 4].forEach(r => sheet.getRange(r, 1, 1, 2).setFontWeight('bold').setBackground('#f1f3f4'));
  sheet.setColumnWidth(1, 200); sheet.setColumnWidth(2, 80);
  sheet.getRange(1, 2, rows.length, 1).setHorizontalAlignment('center');
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
 * 수식으로 쓰고 싶을 때: =YT_META(C2) → 제목 · 채널 · 썸네일 URL 3칸
 * @param {string} url 유튜브 링크
 * @return {string[][]}
 * @customfunction
 */
function YT_META(url) {
  const m = fetchMeta(url);
  return m ? [[m.title, m.channel, m.thumbnail]] : [['', '', '']];
}
