// node tests/test_board_script.js — Apps Script behavior without a sheet/account.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ctx = vm.createContext({assert, Date, console});
vm.runInContext(fs.readFileSync('sheets/Code.gs','utf8'),ctx);
vm.runInContext(`
let uuidCount = 0, syncFail = false, locked = false;
Utilities = {getUuid:() => 'uuid-' + (++uuidCount), formatDate:() => '2026-09-12 10:00'};
LockService = {getScriptLock:() => ({tryLock:()=>!locked,releaseLock:()=>{}})};
SpreadsheetApp = {CopyPasteType:{PASTE_FORMAT:1,PASTE_DATA_VALIDATION:2},getActiveSpreadsheet:()=>ss};
jsonOut = value => value;
refSyncBoardVideos = () => { if(syncFail) throw Error('sync failed'); };
ensureBoardFilter = () => {};
fetchMeta = url => url.includes('bad') ? null : {title:'Fetched',channel:'Channel'};
class Sheet {
  constructor(rows=[]) {
    this.rows = Array.from({length:30},()=>Array(LAST_COL).fill(''));
    COLUMNS.forEach((c,i)=>this.rows[1][i]=c.header);
    rows.forEach((r,i)=>Object.entries(r).forEach(([k,v])=>this.rows[i+2][COL[k]-1]=v));
    this.formulas = new Map();
  }
  getLastRow(){let last=2; this.rows.forEach((r,i)=>{if(r.some(v=>v!=='' && v!==false))last=i+1});return last;}
  getMaxRows(){return this.rows.length;}
  getMaxColumns(){return LAST_COL;}
  setRowHeight(){}
  insertRowBefore(r){this.rows.splice(r-1,0,Array(LAST_COL).fill(''));}
  deleteRow(r){this.rows.splice(r-1,1);}
  getRange(r,c,h=1,w=1){
    const sheet=this;
    return {
      getValues:()=>Array.from({length:h},(_,i)=>sheet.rows[r+i-1].slice(c-1,c+w-1)),
      getValue:()=>sheet.rows[r-1][c-1],
      setValue(v){sheet.rows[r-1][c-1]=v;return this;},
      clearContent(){for(let i=0;i<h;i++)for(let j=0;j<w;j++)sheet.rows[r+i-1][c+j-1]='';return this;},
      setFormula(v){sheet.formulas.set(r+':'+c,v);return this;},
      copyTo(){},
    };
  }
}
let sheet = new Sheet([{dish:'existing'},{youtubeOn:false},{dish:'already',itemId:'keep'}]);
const ss = {getId:()=> 'sheet',getSheetByName:()=>sheet,getSpreadsheetTimeZone:()=> 'Asia/Seoul',toast:()=>{throw Error('no UI')}};
ensureBoardIds(sheet);
assert.equal(sheet.rows[2][COL.itemId-1],'uuid-1');
assert.equal(sheet.rows[3][COL.itemId-1],'');
assert.equal(sheet.rows[4][COL.itemId-1],'keep');
ensureBoardIds(sheet);assert.equal(uuidCount,1);
assert.equal(boardLocateId(sheet,'keep'),5);
sheet.insertRowBefore(3);assert.equal(boardLocateId(sheet,'keep'),6);
sheet.rows[3][COL.itemId-1]='keep';assert.equal(boardLocateId(sheet,'keep'),0);
safeToast('safe');
// Clearing content may leave timestamps, IDs and unchecked checkbox cells.
const emptyRows = new Sheet([{updatedAt:'2026-09-11 20:48',youtubeOn:false},
  {itemId:'orphan',updatedAt:'2026-09-11 20:48'}, {memo:'Keep me'},
  {createdAt:'2026-09-12 10:00'}]);
ensureBoardIds(emptyRows);
ensureBoardRowId(emptyRows,3);
assert.equal(emptyRows.rows[2][COL.itemId-1],'');
assert.equal(emptyRows.rows[3][COL.itemId-1],'orphan');
assert.ok(emptyRows.rows[4][COL.itemId-1]);
assert.ok(emptyRows.rows[5][COL.itemId-1]);
const candidate = {status:STATUSES[0].label,youtubeOn:false,youtubeUrl:''};
assert.equal(boardValidateFields({youtubeOn:true},candidate).error,'locked_platform');
assert.equal(boardValidateFields({status:'촬영 완료',youtubeOn:true},candidate).error,'locked_platform');
assert.equal(boardValidateFields({status:'촬영 완료'},candidate).writes[0].value,'🎥 촬영 완료');
assert.equal(boardValidateFields({youtubeOn:'false'},candidate).error,'bad_fields');
assert.equal(boardValidateFields({status:'wrong'},candidate).error,'bad_status');
assert.equal(boardValidateFields({itemId:'x'},candidate).error,'bad_fields');
assert.equal(boardValidateFields({createdAt:'x'},candidate).error,'bad_fields');
assert.equal(boardValidateFields({desc:'a'.repeat(20001)},candidate).error,'bad_fields');
assert.equal(boardValidateFields({dish:' ==SUM(A1)'},candidate).writes[0].value,'SUM(A1)');
assert.equal(boardValidateFields({refChannels:'\\nChannel\\n'},candidate).writes[0].value,'\\nChannel\\n');
assert.equal(boardValidateFields({status:'업로드 완료',youtubeOn:true},candidate).writes.length,2);
assert.equal(boardValidateFields({youtubeOn:false},{...candidate,youtubeOn:true}).writes[0].value,false);
assert.equal(boardValidateFields({status:'촬영 후보'}, {status:UPLOADED,youtubeOn:true}).writes.length,1);

sheet = new Sheet([{itemId:'a',dish:'Old',status:STATUSES[0].label,srcUrl:'https://old',srcTitle:'Old title'}]);
const call = (action,extra={}) => boardAction({action,sheetId:'sheet',row:3,itemId:'a',...extra});
let r=call('board_update',{fields:{dish:'New'}});
assert.equal(r.ok,true);assert.equal(r.cells.dish,'New');assert.equal(r.cells.itemId,'a');
assert.equal(call('board_update',{fields:{dish:'New'}}).changed.length,0);
assert.equal(call('board_update',{fields:{}}).error,'bad_fields');
assert.equal(call('board_update',{itemId:'missing',fields:{dish:'No'}}).error,'row_mismatch');
assert.equal(call('board_update',{row:2,fields:{dish:'No'}}).error,'bad_row');
locked=true;assert.equal(call('board_update',{fields:{dish:'No'}}).error,'busy');locked=false;
r=call('board_update',{fields:{srcUrl:'https://new',srcTitle:'Manual'}});
assert.equal(r.cells.srcTitle,'Fetched');
r=call('board_update',{fields:{srcUrl:'https://bad',srcTitle:'Manual'}});
assert.equal(r.ok,true);assert.equal(r.cells.srcTitle,'Manual');assert.equal(r.warnings.length,1);
r=call('board_update',{fields:{refUrls:'https://bad',refChannels:'Keep\\nExtra'}});
assert.equal(r.ok,true);assert.equal(r.cells.refChannels,'Keep\\nExtra');assert.equal(r.warnings.length,1);
r=call('board_update',{fields:{srcUrl:'',refUrls:''}});
assert.equal(r.cells.srcTitle,'');assert.equal(r.cells.srcChannel,'');assert.equal(r.cells.refChannels,'');
syncFail=true;r=call('board_update',{fields:{status:'업로드 완료',youtubeOn:true}});
assert.equal(r.ok,true);assert.equal(r.cells.youtubeOn,true);assert.equal(r.warnings.length,1);syncFail=false;
r=call('board_update',{fields:{status:'촬영 후보'}});assert.equal(r.cells.youtubeOn,true);
// Validate before insertion, then create a blank, dated, identifiable row.
const len=sheet.rows.length;
assert.equal(call('board_add',{fields:{youtubeOn:true}}).error,'locked_platform');
assert.equal(sheet.rows.length,len);
r=call('board_add');assert.equal(r.ok,true);assert.equal(r.row,3);assert.ok(r.cells.itemId);assert.ok(r.cells.createdAt);
assert.equal(boardLocateId(sheet,'a'),4);
const added=r.cells.itemId;
r=call('board_delete',{itemId:added});assert.equal(r.ok,true);assert.equal(r.cells.itemId,added);
assert.equal(boardLocateId(sheet,'a'),3);
// Existing candidate path assigns IDs and carries the reference description / pinned comment as drafts.
boardAddCandidate(sheet,'12345678901','Candidate','Channel',{desc:'=참고 설명',pinned:'고정 댓글'});
assert.ok(sheet.rows[2][COL.itemId-1]);assert.equal(sheet.rows[2][COL.srcDesc-1],'참고 설명');assert.equal(sheet.rows[2][COL.srcPinned-1],'고정 댓글');
assert.equal(sheet.rows[2][COL.desc-1]||'','');assert.equal(sheet.rows[2][COL.pinned-1]||'','');
boardAddCandidate(sheet,'12345678902','Plain','Channel');
assert.equal(sheet.rows[2][COL.srcDesc-1]||'','');assert.equal(sheet.rows[2][COL.srcPinned-1]||'','');
console.log('Board Apps Script ID, validation, CRUD, metadata and formula checks passed.');
`,ctx);
