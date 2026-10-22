const vm=require('node:vm');
const context=require('./test_upload_script.js');
vm.runInContext(`
function cookBody(){return {...imageBody(),cookDict:{version:1,title:'마늘밥',author:'',sourceChannel:'채널',description:'설명',category:'one_bowl',difficulty:null,baseServings:null,
 ingredients:[{name:'마늘',amount:'1/2',unit:'큰술',note:null,isMain:true}],steps:['볶는다.']},cookPhoto:{mime:'image/jpeg',data:'/9j/'}};}
reset();let cb=cookBody(), cr=uploadAction(cb);assert.ok(cr.ok);assert.equal(files.length,2);assert.equal(batchCount,1);
assert.equal(cr.cells.cookVersion,'1');assert.equal(JSON.parse(cr.cells.cookIngredients)[0].amount,'1/2');assert.ok(cr.cells.cookPhoto);
cr=uploadAction(cb);assert.ok(cr.submitted);assert.equal(files.length,2);assert.equal(batchCount,1);
assert.equal(uploadAction({...cb,cookDict:{...cb.cookDict,title:'changed'}}).error,'request_mismatch');
properties.COOK_DICT_READ_TOKEN='reader';
assert.equal(cookDictList({token:'private',sheetId:'sheet'}).error,'unauthorized');
let page=cookDictList({token:'reader',sheetId:'sheet'});assert.equal(page.items.length,1);assert.equal(page.items[0].recipe.photoUrl,cr.cells.cookPhoto);
assert.equal(page.items[0].recipe.author,'');assert.equal(page.items[0].itemId,'item');
sheet.rows[3][COL.itemId-1]='item';page=cookDictList({token:'reader',sheetId:'sheet'});assert.equal(page.items.length,0);assert.equal(page.errors.length,1);
reset();cb=cookBody();mode='invalid';cr=uploadAction(cb);assert.equal(cr.error,'commit_failed');assert.ok(files.every(f=>f.trashed));assert.equal(boardReadRow(sheet,3).cookVersion,'');
reset();cb=cookBody();mode='timeoutBefore';cr=uploadAction(cb);assert.equal(cr.error,'commit_unknown');assert.ok(files.every(f=>!f.trashed));
mode='';cr=uploadAction(cb);assert.ok(cr.submitted);assert.equal(files.length,2);
reset();cb=cookBody();cb.cookPhoto.data='bad';assert.equal(uploadAction(cb).error,'bad_fields');assert.equal(batchCount,0);
reset();cb=cookBody();mode='timeoutAfter';cr=uploadAction(cb);assert.ok(cr.submitted);assert.equal(files.length,2);
reset();cb=cookBody();sheet.rows[1][COL.cookUpdated-1]='old';assert.equal(uploadAction(cb).error,'upgrade_required');assert.equal(files.length,0);
// Reinstall replaces only our timer and runs exactly every six hours.
properties.COOK_DICT_SITE_URL='https://cook-dict.example';
properties.COOK_DICT_CRON_SECRET='isolated-test-secret';
let triggers=[{getHandlerFunction:()=> 'otherJob'}, {getHandlerFunction:()=> 'runCookDictSync'}],hours,alerts=[];
ScriptApp={getProjectTriggers:()=>triggers,deleteTrigger:t=>{triggers=triggers.filter(x=>x!==t)},
 newTrigger:handler=>({timeBased:()=>({everyHours:n=>{hours=n;return {create:()=>triggers.push({getHandlerFunction:()=>handler})}}})})};
SpreadsheetApp.getUi=()=>({alert:s=>alerts.push(s)});
installCookDictSync();installCookDictSync();
assert.equal(hours,6);assert.equal(triggers.length,2);assert.equal(triggers.filter(t=>t.getHandlerFunction()==='runCookDictSync').length,1);
let request;UrlFetchApp={fetch:(url,options)=>{request={url,options};return {getResponseCode:()=>200}}};
runCookDictSync();assert.equal(request.url,'https://cook-dict.example/api/cron/import-recipes');
assert.equal(request.options.headers.Authorization,'Bearer isolated-test-secret');assert.equal(request.options.followRedirects,false);
UrlFetchApp.fetch=()=>({getResponseCode:()=>503});assert.throws(runCookDictSync,/HTTP 503/);
delete properties.COOK_DICT_CRON_SECRET;assert.throws(installCookDictSync);assert.equal(triggers.length,2);
console.log('Cook-dict columns, feed authentication, duplicate IDs, photo atomicity, retries and six-hour scheduler passed.');
`,context);
