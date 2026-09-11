// Run with: node tests/test_reference_layout.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync('sheets/Code.gs', 'utf8'), ctx);
vm.runInContext(`
  const oldHeaders = ['🖼️ 프로필', '📺 채널', '👥 구독자', '📅 추가 시각', '📦 쇼츠 목록', '🕒 분석 저장 시각', '🔗 프로필 이미지 URL', '📝 채널 설명', '🆔 채널 ID'];
  const oldValues = ['', '테스트', 112000, '', '✅ 완료', '', 'https://example.com/a.jpg', '소개', 'UC_TEST'];
  const oldSheet = {
    getLastRow: () => 2,
    getRange: () => ({
      getValues: () => [oldValues],
      getRichTextValues: () => [oldValues.map((_, i) => ({ getLinkUrl: () => i === 1 ? 'https://youtube.com/@test' : null }))],
    }),
  };
  const migrated = refReadByHeader(oldSheet, oldHeaders.map(stripEmoji), REF_CHANNEL_COLUMNS, REF_CHANNEL_LEGACY_HEADERS)[0];
`, ctx);
assert.equal(vm.runInContext('migrated.subscriber_count', ctx), 112000);
assert.equal(vm.runInContext('migrated.url', ctx), 'https://youtube.com/@test');
assert.equal(vm.runInContext("refCell(migrated.shorts_complete, REF_CHANNEL_COLUMNS.find(c => c.flag), migrated)", ctx), '✅ 완료');
assert.equal(vm.runInContext('refCompactFormat(112000)', ctx), '[=112000]"11.2만";[<0]-#,##0;#,##0');
assert.equal(vm.runInContext('refCompactFormat(10000)', ctx), '[=10000]"1만";[<0]-#,##0;#,##0');
for (const value of [0, 9999, null]) {
  assert.equal(vm.runInContext(`refCompactFormat(${JSON.stringify(value)})`, ctx), '#,##0');
}
vm.runInContext(`
  const output = {};
  const channelsSheet = {};
  SpreadsheetApp = {
    getActiveSpreadsheet: () => ({getSheetByName: () => channelsSheet}),
    newRichTextValue: () => ({setText() {return this;}, setLinkUrl() {return this;}, build() {return {};}}),
  };
  refRows = () => [{item: {id: 'UC_TEST', name: '테스트 채널'}}];
  const target = {
    getMaxRows: () => 20,
    getRange: (r, c, h, w) => ({
      setValues: values => {output.matrix = values;},
      setNumberFormats: values => {output['format' + c] = values;},
      setFormulas: values => {output['formula' + c] = values;},
      setRichTextValues: () => {},
    }),
  };
  refWriteRows(target, REF_SHORT_COLUMNS, 2, [{
    channel_id: 'UC_TEST', video_id: 'vid', title: '제목', url: 'https://youtube.com/shorts/vid',
    view_count: 112000, like_count: 15000, comment_count: 10, board_status: '⭐ 촬영 후보',
  }]);
`, ctx);
assert.equal(vm.runInContext('output.matrix[0][2]', ctx), '테스트 채널');
assert.equal(vm.runInContext('output.matrix[0][4]', ctx), 112000);
assert.equal(vm.runInContext('output.matrix[0][5]', ctx), 15000);
assert.equal(vm.runInContext('output.matrix[0][0]', ctx), '⭐ 촬영 후보');
assert.equal(vm.runInContext('output.formula2[0][0]', ctx), '=IF(N2="","",IMAGE(N2))');
assert.equal(vm.runInContext('output.format5[0][0]', ctx), '[=112000]"11.2만";[<0]-#,##0;#,##0');
assert.equal(vm.runInContext("REF_SHORT_COLUMNS.some(c => c.key === 'pinned_comment_author')", ctx), false);
console.log('Reference layout migration and sync checks passed.');
