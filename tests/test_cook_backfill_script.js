const vm = require('node:vm');
const context = require('./test_upload_script.js');
vm.runInContext(`
function backfillSeed() { reset();sheet.rows[2][COL.status-1]=UPLOADED; }
function backfillBody() {
 const c=boardReadRow(sheet,3);
 return {action:'cook_backfill_save',sheetId:'sheet',itemId:'item',revision:cookBackfillRevision(c),
  cookDict:{version:1,title:'마늘밥',author:'',sourceChannel:c.srcChannel||'',description:c.desc,
   category:null,difficulty:null,baseServings:null,
   ingredients:[{name:'마늘',amount:'1/2',unit:'큰술',note:null,isMain:true}],steps:['볶는다.']}};
}
backfillSeed();let list=cookBackfillAction({action:'cook_backfill_list',sheetId:'sheet'});
assert.equal(list.items.length,1);assert.equal(batchCount,0);assert.equal(list.nextCursor,null);
let bf=backfillBody(),before=boardReadRow(sheet,3);
assert.equal(cookBackfillAction({...bf,action:'cook_backfill_get'}).item.description,before.desc);
assert.ok(cookBackfillAction(bf).saved);assert.equal(batchCount,1);assert.equal(files.length,0);
let after=boardReadRow(sheet,3);
for(const key of Object.keys(before))if(!COOK_KEYS.includes(key))assert.equal(after[key],before[key],key);
assert.equal(after.cookPhoto,'');assert.equal(after.cookVersion,'1');
assert.equal(JSON.parse(after.cookIngredients)[0].amount,'1/2');
assert.ok(cookBackfillAction(bf).skipped);assert.equal(batchCount,1);
properties.COOK_DICT_READ_TOKEN='reader';
assert.equal(cookDictList({sheetId:'sheet',token:'reader'}).items[0].recipe.photoUrl,'');
list=cookBackfillAction({action:'cook_backfill_list',sheetId:'sheet'});assert.equal(list.items.length,0);assert.equal(list.skipped,1);
// Partial manual input is protected, even without a version marker.
backfillSeed();bf=backfillBody();sheet.rows[2][COL.cookIngredients-1]='manual work';
assert.ok(cookBackfillAction(bf).skipped);assert.equal(batchCount,0);
backfillSeed();bf=backfillBody();sheet.rows[2][COL.desc-1]='edited during extraction';
assert.equal(cookBackfillAction(bf).error,'conflict');assert.equal(batchCount,0);
backfillSeed();bf=backfillBody();sheet.rows[2][COL.status-1]=STATUSES[0].label;
assert.equal(cookBackfillAction(bf).error,'conflict');assert.equal(batchCount,0);
backfillSeed();sheet.rows[2][COL.desc-1]='';bf=backfillBody();
assert.equal(cookBackfillAction({...bf,action:'cook_backfill_get'}).error,'cook_description_required');
// A moved row is located by ID; platform edits never get replaced.
backfillSeed();bf=backfillBody();sheet.rows.splice(2,0,Array(LAST_COL).fill(''));sheet.rows[3][COL.instagramUrl-1]='https://instagram.com/reel/kept';
assert.ok(cookBackfillAction(bf).saved);assert.equal(boardReadRow(sheet,4).instagramUrl,'https://instagram.com/reel/kept');
// Lost responses and failed batches are safe to retry.
for(const failure of ['invalid','timeoutBefore','timeoutAfter']) {
 backfillSeed();bf=backfillBody();mode=failure;
 assert.equal(cookBackfillAction(bf).error,'cook_backfill_unknown');
 if(failure!=='timeoutAfter')assert.equal(boardReadRow(sheet,3).cookVersion,'');
 mode='';const retry=cookBackfillAction(bf);assert.ok(retry.saved||retry.skipped);
 assert.equal(boardReadRow(sheet,3).cookVersion,'1');
}
// Writer authorization is mandatory; the import-only token cannot backfill.
backfillSeed();properties.UPLOAD_TOKEN='writer';properties.COOK_DICT_READ_TOKEN='reader';bf=backfillBody();
assert.equal(doPost({postData:{contents:JSON.stringify({...bf,token:'reader'})}}).error,'unauthorized');assert.equal(batchCount,0);
assert.ok(doPost({postData:{contents:JSON.stringify({...bf,token:'writer'})}}).saved);
// Pagination checks duplicate IDs beyond the current page.
backfillSeed();while(sheet.rows.length<80)sheet.rows.push(Array(LAST_COL).fill(''));
sheet.rows[70][COL.itemId-1]='item';sheet.rows[70][COL.status-1]=UPLOADED;
list=cookBackfillAction({action:'cook_backfill_list',sheetId:'sheet'});assert.equal(list.nextCursor,53);assert.equal(list.items.length,0);assert.equal(list.errors.length,1);
list=cookBackfillAction({action:'cook_backfill_list',sheetId:'sheet',cursor:53});assert.equal(list.nextCursor,null);assert.equal(list.errors.length,1);
console.log('Backfill eligibility, auth, atomicity, retries, empty photos and preservation passed.');
`, context);
