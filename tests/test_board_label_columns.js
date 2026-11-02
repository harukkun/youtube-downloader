// node tests/test_board_label_columns.js — 인플루언서·카테고리 열이 현황판 열 정의/필터(정렬) 범위에 들어갔는지 확인한다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ctx = vm.createContext({assert, Date, console});
vm.runInContext(fs.readFileSync('sheets/Code.gs', 'utf8'), ctx);
vm.runInContext(`
// 1) 열 위치: 기존 열(…요리사전 데이터 수정일=AG)은 그대로, 요리 분류 두 열이 AH·AI 로 끝에 붙는다.
assert.equal(LAST_COL, 35);
assert.equal(colLetter(COL.itemId), 'AA');
assert.equal(colLetter(COL.cookBasic), 'AB');
assert.equal(colLetter(COL.cookUpdated), 'AG');
assert.equal(colLetter(COL.influencer), 'AH');
assert.equal(colLetter(COL.category), 'AI');
// 웹 현황판 동기화는 헤더 이름으로 읽는다: 라이브 시트 헤더(이모지 포함)가 키와 같은 이름으로 환산돼야 한다.
assert.equal(headerKeyName('👤 인플루언서'), '인플루언서');
assert.equal(headerKeyName('🍽️ 카테고리'), '카테고리');
assert.equal(headerKeyName(COLUMNS[COL.influencer - 1].header), '인플루언서');
assert.equal(headerKeyName(COLUMNS[COL.category - 1].header), '카테고리');
assert.equal(COLUMNS[COL.influencer - 1].group, 'label');
assert.ok(GROUPS.label);

// 2) 필터(=정렬) 범위가 AI(35열)까지 덮는다.
let created = null;
const filterSheet = {
  getFilter: () => null, getMaxRows: () => 100,
  getRange: (r, c, h, w) => ({createFilter: () => { created = {r, c, h, w}; return {setColumnFilterCriteria() {}}; }}),
};
ensureBoardFilter(filterSheet);
assert.deepEqual(created, {r: HEADER_ROW, c: 1, h: 100 - HEADER_ROW + 1, w: 35});

// 3) 필터 범위 폭으로 정렬을 흉내 내면 라벨이 행과 함께 움직인다 (옛 폭 33 이면 어긋난다 = 회귀 방지).
function sortByStatus(rows, width) {
  const body = rows.map(r => r.slice(0, width)), order = body.map((_, i) => i)
    .sort((a, b) => String(body[a][0]).localeCompare(String(body[b][0])));
  return rows.map((row, i) => row.slice(0, 0).concat(body[order[i]], row.slice(width)));
}
const mk = (status, inf) => { const r = Array(LAST_COL).fill(''); r[0] = status; r[COL.influencer - 1] = inf; return r; };
const data = [mk('b', '류수영'), mk('a', '백종원'), mk('c', '성시경')];
const good = sortByStatus(data, created.w);
assert.deepEqual(good.map(r => [r[0], r[COL.influencer - 1]]), [['a', '백종원'], ['b', '류수영'], ['c', '성시경']]);
const legacy = sortByStatus(data, 33);
assert.notDeepEqual(legacy.map(r => [r[0], r[COL.influencer - 1]]), [['a', '백종원'], ['b', '류수영'], ['c', '성시경']]);

// 4) 레이아웃 마이그레이션
class LayoutSheet {
  constructor(headers, data) { this.rows = [headers.map((_, i) => i ? '' : '📋 기본'), headers.slice(), data.slice()]; this.moves = 0; this.inserts = 0; }
  width() { return this.rows[1].length; }
  getLastColumn() { return this.width(); } getMaxColumns() { return this.width(); } getLastRow() { return this.rows.length; }
  getRange(r, c, h = 1, w = 1) {
    const rows = this.rows, cells = () => rows.slice(r - 1, r - 1 + h).map(row => row.slice(c - 1, c - 1 + w));
    return {col: c, getValue: () => rows[r - 1][c - 1], getValues: cells, getFormulas: () => cells().map(row => row.map(() => '')),
      setValues: vals => vals.forEach((row, i) => row.forEach((v, j) => rows[r - 1 + i][c - 1 + j] = v)),
      breakApart() {}, clearDataValidations() {}, clearContent() {}};
  }
  deleteColumn(c) { this.rows.forEach(row => row.splice(c - 1, 1)); }
  insertColumnBefore(c) { this.inserts++; this.rows.forEach(row => row.splice(c - 1, 0, '')); }
  insertColumnsAfter(c, n) { this.inserts++; this.rows.forEach(row => row.splice(c, 0, ...Array(n).fill(''))); }
  moveColumns(range, target) { this.moves++; this.rows.forEach(row => { const [v] = row.splice(range.col - 1, 1); row.splice(target - 1, 0, v); }); }
}
const valueOf = c => c.key === 'status' ? '🎬 촬영 중' : c.checkbox ? false : 'v-' + c.key;
// 4-a) AH·AI 가 이미 있는 현재 라이브 시트 → 이동/삽입 없이 그대로(데이터 보존)
const live = new LayoutSheet(COLUMNS.map(c => c.header), COLUMNS.map(valueOf));
migrateLayout(live);
assert.equal(live.moves, 0); assert.equal(live.inserts, 0); assert.equal(live.width(), 35);
assert.equal(live.rows[2][COL.influencer - 1], 'v-influencer'); assert.equal(live.rows[2][COL.category - 1], 'v-category');
// 4-b) 라벨 열이 없는 옛 시트(33열) → 끝에 빈 열 2개가 붙고 기존 데이터는 그대로
const old = COLUMNS.filter(c => c.group !== 'label');
const oldSheet = new LayoutSheet(old.map(c => c.header), old.map(valueOf));
migrateLayout(oldSheet);
assert.equal(oldSheet.width(), 35);
assert.equal(oldSheet.rows[2][COL.itemId - 1], 'v-itemId'); assert.equal(oldSheet.rows[2][COL.cookUpdated - 1], 'v-cookUpdated');
assert.equal(oldSheet.rows[2][COL.influencer - 1], ''); assert.equal(oldSheet.rows[2][COL.category - 1], '');
// 4-c) 라벨 열이 사용자가 끌어서 다른 자리에 있어도 AH·AI 로 정리되고 값이 따라간다
const shuffled = COLUMNS.slice(); const [infl] = shuffled.splice(COL.influencer - 1, 1); shuffled.splice(4, 0, infl);
const shufSheet = new LayoutSheet(shuffled.map(c => c.header), shuffled.map(valueOf));
migrateLayout(shufSheet);
COLUMNS.forEach((c, i) => assert.equal(headerKeyName(shufSheet.rows[1][i]), headerKeyName(c.header), c.key));
assert.equal(shufSheet.rows[2][COL.influencer - 1], 'v-influencer');

// 5) 라벨만 채워진 빈 행에는 항목 ID 를 만들지 않고, 웹 편집 API 대상에서도 빠진다.
const labelOnly = Array(LAST_COL).fill(''); labelOnly[COL.influencer - 1] = '류수영'; labelOnly[COL.category - 1] = '국';
assert.equal(boardHasContent(labelOnly), false);
const withDish = labelOnly.slice(); withDish[COL.dish - 1] = '요리'; assert.equal(boardHasContent(withDish), true);
assert.equal(BOARD_EDITABLE.has('influencer'), false); assert.equal(BOARD_EDITABLE.has('category'), false);
assert.equal(BOARD_EDITABLE.has('cookBasic'), true);   // 기존 동작 유지
`, ctx);
console.log('Board label columns (인플루언서·카테고리) layout, filter range and migration checks passed.');
