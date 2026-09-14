// Offline Apps Script integration: real uploadAction, mocked atomic Sheets/Drive services.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const context=vm.createContext({console,Date,assert,hash:s=>[...crypto.createHash('sha256').update(s).digest()],decode:s=>[...Buffer.from(s,'base64')]});
vm.runInContext(fs.readFileSync('sheets/Code.gs','utf8'),context);
vm.runInContext(`
Utilities={DigestAlgorithm:{SHA_256:1},Charset:{UTF_8:1},computeDigest:(_,s)=>hash(s),base64Decode:decode,
 formatDate:(_,tz,fmt)=>fmt.includes("'T'")?'2026-09-12T12:30:00':'2026-09-12 12:30',newBlob:(bytes,mime,name)=>({bytes,mime,name})};
let locked=false,properties={},files=[],batchCount=0,mode='',receipts=[],syncFail=false;
PropertiesService={getScriptProperties:()=>({getProperty:k=>properties[k]||null,setProperty:(k,v)=>properties[k]=v,deleteProperty:k=>delete properties[k]})};
LockService={getScriptLock:()=>({tryLock:()=>!locked,releaseLock:()=>{}})};
class Sheet {
 constructor(){this.rows=Array.from({length:20},()=>Array(LAST_COL).fill(''));this.formulas={};COLUMNS.forEach((c,i)=>this.rows[1][i]=c.header);}
 getSheetId(){return 7} getMaxColumns(){return LAST_COL} getLastRow(){return this.rows.length}
 getRange(r,c,h=1,w=1){return {getValues:()=>this.rows.slice(r-1,r-1+h).map(row=>row.slice(c-1,c-1+w)),getValue:()=>this.rows[r-1][c-1]};}
}
let sheet;
const ss={getId:()=> 'sheet',getSheetByName:()=>sheet,getSpreadsheetTimeZone:()=> 'Asia/Seoul'};
SpreadsheetApp={getActiveSpreadsheet:()=>ss};jsonOut=value=>value;
fetchMeta=url=>url.includes('bad')?null:{title:'Fetched',channel:'Channel'};
refSyncBoardVideos=()=>{if(syncFail)throw Error('sync');};
const folder={createFile:blob=>{const file={id:'file'+files.length,trashed:false,getId(){return this.id},setSharing(){if(mode==='sharing')throw Error('sharing')},setTrashed(v){this.trashed=v},isTrashed(){return this.trashed}};files.push(file);return file;}};
thumbFolder=()=>folder;trashOldThumb=()=>{};
DriveApp={Access:{ANYONE_WITH_LINK:1},Permission:{VIEW:1},getFileById:id=>files.find(f=>f.id===id)};
Sheets={Spreadsheets:{Values:{get:(_,range,options)=>{assert.equal(options.valueRenderOption,'UNFORMATTED_VALUE');const row=Number(range.match(/!A(\\d+):/)[1]);return {values:[sheet.rows[row-1].slice()]};}},DeveloperMetadata:{search:body=>{
 if(mode==='searchFail')throw Error('network');
 return {matchedDeveloperMetadata:receipts.filter(r=>r.metadataKey===body.dataFilters[0].developerMetadataLookup.metadataKey).map(r=>({developerMetadata:r}))};
}},batchUpdate:body=>{
 batchCount++;
 if(mode==='invalid')throw Error('Invalid requests[0]: invalid range');
 if(mode==='timeoutBefore')throw Error('network timeout');
 const metadata=body.requests.find(r=>r.createDeveloperMetadata).createDeveloperMetadata.developerMetadata;
 if(receipts.some(r=>r.metadataId===metadata.metadataId))throw Error('metadata already exists');
 // Validate every request before applying any writes, matching Sheets batchUpdate semantics.
 for(const r of body.requests)if(r.updateCells){assert.equal(r.updateCells.fields,'userEnteredValue');assert.equal(r.updateCells.range.sheetId,7);}
 for(const r of body.requests)if(r.updateCells){const u=r.updateCells,v=u.rows[0].values[0].userEnteredValue;
   const row=u.range.startRowIndex,col=u.range.startColumnIndex;
   if(v.formulaValue)sheet.formulas[row+':'+col]=v.formulaValue;
   else sheet.rows[row][col]=v.stringValue===undefined?v.numberValue:v.stringValue;
 }
 receipts.push(metadata);
 if(mode==='timeoutAfter')throw Error('network timeout after commit');
}}};
function reset(){sheet=new Sheet();locked=false;properties={};files=[];batchCount=0;mode='';receipts=[];syncFail=false;
 Object.entries({itemId:'item',status:STATUSES[2].label,dish:'Dish',title:'old',desc:'old desc',srcUrl:'https://youtu.be/abcdefghijk',refUrls:'https://youtu.be/lmnopqrstuv',memo:'',thumbUrl:'https://old/image',youtubeOn:true,youtubeUrl:'https://my/video',pinned:'keep pinned',createdAt:'keep date'}).forEach(([k,v])=>sheet.rows[2][COL[k]-1]=v);
}
function body(){return {action:'upload_submit',sheetId:'sheet',itemId:'item',requestId:'12345678-1234-1234-1234-123456789abc',revision:uploadRevision(boardReadRow(sheet,3)),thumbnailMode:'existing',fields:{title:'new',desc:'new description',srcUrl:'https://youtu.be/abcdefghijk',refUrls:'https://youtu.be/lmnopqrstuv',memo:'note'}};}
function imageBody(){return {...body(),thumbnailMode:'new',mime:'image/jpeg',data:'/9j/'};}
reset();let b=body();let r=uploadAction({...b,action:'upload_get',requestId:''});assert.ok(r.ok);assert.equal(r.revision,b.revision);assert.equal(batchCount,0);
// The ID follows moved rows and unrelated platform edits do not conflict.
sheet.rows.splice(2,0,Array(LAST_COL).fill(''));sheet.rows[3][COL.youtubeUrl-1]='changed by teammate';
r=uploadAction(b);assert.ok(r.ok);assert.equal(r.row,4);assert.equal(r.cells.title,'new');assert.equal(r.cells.status,UPLOADED);
assert.equal(r.cells.youtubeUrl,'changed by teammate');assert.equal(r.cells.pinned,'keep pinned');assert.equal(r.cells.createdAt,'keep date');assert.equal(batchCount,1);
r=uploadAction(b);assert.ok(r.submitted);assert.equal(batchCount,1);
r=uploadAction({...b,fields:{...b.fields,title:'different'}});assert.equal(r.error,'request_mismatch');
r=uploadAction({...b,action:'upload_get'});assert.ok(r.submitted);assert.equal(batchCount,1);
reset();b=body();sheet.rows[2][COL.desc-1]='teammate';assert.equal(uploadAction(b).error,'conflict');assert.equal(batchCount,0);
reset();b=body();sheet.rows[2][COL.status-1]=UPLOADED;assert.equal(uploadAction(b).error,'already_uploaded');
reset();b=body();sheet.rows[2][COL.itemId-1]='deleted';assert.equal(uploadAction(b).error,'row_mismatch');
reset();b=body();sheet.rows[3][COL.itemId-1]='item';assert.equal(uploadAction(b).error,'row_mismatch');
reset();b=body();locked=true;assert.equal(uploadAction(b).error,'busy');assert.equal(batchCount,0);
reset();b=body();assert.equal(uploadAction({...b,sheetId:'other'}).error,'wrong_sheet');
assert.equal(uploadAction({...b,fields:{...b.fields,status:UPLOADED}}).error,'bad_fields');
assert.equal(uploadAction({...b,fields:{...b.fields,title:''}}).error,'bad_fields');
assert.equal(uploadAction({...b,fields:{...b.fields,title:'a'.repeat(501)}}).error,'bad_fields');
reset();b=imageBody();r=uploadAction(b);assert.ok(r.ok);assert.equal(files.length,1);assert.equal(batchCount,1);assert.equal(r.cells.thumbUrl,thumbUrlFor('file0'));
r=uploadAction(b);assert.ok(r.ok);assert.equal(files.length,1);assert.equal(batchCount,1);
reset();b=imageBody();mode='sharing';r=uploadAction(b);assert.equal(r.error,'image_failed');assert.ok(files[0].trashed);assert.equal(batchCount,0);assert.equal(boardReadRow(sheet,3).title,'old');
reset();b=imageBody();mode='invalid';r=uploadAction(b);assert.equal(r.error,'commit_failed');assert.equal(boardReadRow(sheet,3).title,'old');assert.ok(files[0].trashed);assert.equal(receipts.length,0);
reset();b=imageBody();mode='timeoutAfter';r=uploadAction(b);assert.ok(r.submitted);assert.equal(batchCount,1);assert.equal(files.length,1);assert.equal(files[0].trashed,false);
mode='';r=uploadAction(b);assert.ok(r.submitted);assert.equal(batchCount,1);
reset();b=imageBody();mode='timeoutBefore';r=uploadAction(b);assert.equal(r.error,'commit_unknown');assert.equal(files[0].trashed,false);assert.equal(boardReadRow(sheet,3).title,'old');
mode='';r=uploadAction({...b,action:'upload_get'});assert.equal(r.submitted,false);r=uploadAction(b);assert.ok(r.submitted);assert.equal(files.length,1);assert.equal(batchCount,2);
reset();b=body();syncFail=true;r=uploadAction(b);assert.ok(r.submitted);assert.equal(r.warnings.length,1);
reset();b=body();b.fields.srcUrl='https://bad';r=uploadAction(b);assert.ok(r.submitted);assert.equal(r.warnings.length,1);
reset();const service=Sheets;Sheets=undefined;assert.equal(uploadAction(body()).error,'sheets_service_required');Sheets=service;
console.log('Upload Apps Script atomicity, ID/revision, retry receipts, files and preservation passed.');
`,context);
