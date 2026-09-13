// Start tests/upload_browser_fixture.py first. This uses isolated Chrome profiles and only localhost.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:8877';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const errors=[];
 async function context(viewport={width:1280,height:950}){
  const c=await browser.newContext({viewport});
  await c.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort());
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(10000);return {c,p};
 }
 const setup=await context();let {c,p}=setup;
 const control=data=>p.request.post(base+'/_test/control',{data});
 const status=async()=> (await p.request.get(base+'/_test/control')).json();
 async function choose(index=0){await p.goto(base+'/upload-process');await p.locator('.item-option').nth(index).click();await p.locator('#videoTitle').fill('완성한 김치볶음밥');}
 async function generate(){await p.locator('#nextStep').click();await p.locator('#srcText').fill('김치 100g과 밥 1공기를 식용유 1T에 3분간 볶는다.');await p.locator('#genBtn').click();await p.waitForFunction(()=>RecipeEditor.valid());await p.locator('#confirmRecipe').click();await p.locator('#nextStep').click();}
 await control({reset:true});await choose();await generate();
 const png=await (await p.request.get(base+'/_test/image')).body();
 await p.locator('#file').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:png});
 await p.waitForFunction(()=>!document.getElementById('export').disabled);
 await p.screenshot({path:'/tmp/upload-process-editor.png',fullPage:true});
 await p.locator('#title').fill('김치볶음밥');await p.locator('#subtitle').fill('주말 한 끼');
 await p.locator('#confirmThumbnail').click();await p.waitForFunction(()=>!document.getElementById('confirmedThumb').hidden);
 // Download is independent from the confirmed draft and never registers a thumbnail.
 await Promise.all([p.waitForEvent('download'),p.locator('#export').click()]);
 assert.equal(await p.locator('#nextStep').isDisabled(),false);
 assert.equal((await status()).posts,0);
 await p.locator('#title').fill('더 맛있는 볶음밥');assert.equal(await p.locator('#nextStep').isDisabled(),true);
 await p.locator('#confirmThumbnail').click();await p.waitForFunction(()=>document.getElementById('draftStatus').textContent.includes('저장했습니다'));
 await p.reload();await p.waitForFunction(()=>!document.getElementById('confirmedThumb').hidden);
 assert.equal(await p.locator('#title').inputValue(),''); // Only the finalized image is restored, not the source frame.
 await p.locator('#nextStep').click();await p.locator('#reviewDescription').waitFor({state:'visible'});
 assert.ok((await p.locator('#reviewDescription').innerText()).startsWith('유튜브 레시피'));
 await p.screenshot({path:'/tmp/upload-process-desktop.png',fullPage:true});
 p.once('dialog',d=>d.dismiss());await p.locator('#submitProcess').click();assert.equal((await status()).posts,0);
 await control({mode:'lost'});p.once('dialog',d=>d.accept());await p.locator('#submitProcess').click();await p.locator('#recovery').waitFor({state:'visible'});
 await p.waitForFunction(()=>!document.getElementById('checkSubmission').disabled);
 assert.equal(await p.locator('#videoTitle').isDisabled(),true);
 await p.reload();await p.locator('#completed').waitFor({state:'visible'});
 assert.equal((await status()).posts,1);
 await p.locator('#nextItem').click();await p.waitForFunction(()=>document.querySelectorAll('.item-option').length===1);
 await c.close();
 // Mobile + clarification + existing thumbnail + conflict recovery.
 ({c,p}=await context({width:390,height:844}));await control({reset:true,mode:'questions'});await choose(1);
 await p.locator('#nextStep').click();await p.locator('#srcText').fill('김치와 밥을 볶는다.');await p.locator('#genBtn').click();
 await p.locator('#answer-0').waitFor({state:'visible'});assert.equal(await p.locator('#confirmRecipe').isDisabled(),true);
 await p.screenshot({path:'/tmp/upload-process-recipe-mobile.png',fullPage:true});
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await p.locator('#answer-0').fill('식용유 1T');await p.locator('#answerBtn').click();await p.waitForFunction(()=>RecipeEditor.valid());
 await p.locator('#youtubeOut').fill('직접 다듬은 유튜브 설명');await p.locator('#confirmRecipe').click();await p.locator('#nextStep').click();
 await p.locator('#useExisting').click();await p.locator('#nextStep').click();
 assert.equal(await p.locator('#reviewDescription').innerText(),'직접 다듬은 유튜브 설명');
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await p.screenshot({path:'/tmp/upload-process-mobile.png',fullPage:true});
 await control({mode:'unknown'});p.once('dialog',d=>d.accept());await p.locator('#submitProcess').click();
 await p.waitForFunction(()=>!document.getElementById('checkSubmission').disabled);await p.locator('#checkSubmission').click();await p.locator('#retrySubmission').waitFor({state:'visible'});
 await control({mode:''});await p.locator('#retrySubmission').click();await p.locator('#completed').waitFor({state:'visible'});assert.equal((await status()).posts,2);
 await c.close();
 ({c,p}=await context());await control({reset:true});await choose();await p.waitForFunction(()=>document.getElementById('draftStatus').textContent.includes('저장했습니다'));
 await control({change:true});await p.reload();await p.locator('#conflictBox').waitFor({state:'visible'});
 assert.equal(await p.locator('#videoTitle').inputValue(),'완성한 김치볶음밥');assert.equal(await p.locator('#nextStep').isDisabled(),true);
 p.once('dialog',d=>d.accept());await p.locator('#restartDraft').click();await p.waitForFunction(()=>document.getElementById('videoMemo').value==='팀원이 바꾼 메모');
 await c.close();
 // A result for a previous item must not overwrite the newly selected item's draft.
 ({c,p}=await context());await control({reset:true});await choose();
 await p.locator('#nextStep').click();await p.locator('#srcText').fill('첫 번째 항목의 레시피');
 let release;const delayed=new Promise(resolve=>release=resolve);
 await p.route('**/api/helper/recipe-description',async route=>{await delayed;await route.fulfill({json:{status:'complete',results:{instagram:'OLD',youtube:'OLD',tiktok:'OLD'},notes:[]}});});
 await p.locator('#genBtn').click();await p.locator('[data-goto="0"]').click();await p.locator('.item-option').nth(1).click();
 release();await p.waitForTimeout(200);
 assert.equal(await p.locator('#youtubeOut').inputValue(),'');assert.equal(await p.locator('#srcText').inputValue(),'');
 // Restore an orphaned draft after the source row is deleted without silently discarding it.
 await p.locator('[data-goto="0"]').click();await p.locator('.item-option').nth(0).click();
 await p.waitForFunction(()=>document.getElementById('draftStatus').textContent.includes('저장했습니다'));
 await control({delete:true});await p.reload();await p.locator('#conflictBox').waitFor({state:'visible'});
 assert.equal(await p.locator('#videoTitle').inputValue(),'완성한 김치볶음밥');
 await c.close();
 // Persistence failures are visible; work is still editable in memory.
 ({c,p}=await context());await control({reset:true});
 await p.addInitScript(()=>Object.defineProperty(indexedDB,'open',{value:()=>{throw new Error('quota');}}));
 await choose();await p.waitForFunction(()=>document.getElementById('draftStatus').textContent.includes('보관하지 못했습니다'));
 assert.equal(await p.locator('#videoTitle').inputValue(),'완성한 김치볶음밥');await c.close();
 // Existing helper still initializes, generates and switches tools.
 ({c,p}=await context());await control({reset:true});await p.goto(base+'/helper');await p.locator('#srcText').fill('김치 100g 밥 1공기 식용유 1T를 3분 볶는다.');
 await p.locator('#genBtn').click();await p.waitForFunction(()=>RecipeEditor.valid());
 await p.locator('.tool[data-tool="thumbnail"]').click();await p.locator('#file').waitFor({state:'attached'});await p.locator('#file').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:png});
 await p.waitForFunction(()=>!document.getElementById('export').disabled);
 await Promise.all([p.waitForEvent('download'),p.locator('#export').click()]);
 assert.deepEqual(errors,[]);await c.close();await browser.close();
 console.log('Browser desktop/mobile funnel, drafts, confirmation, lost responses, retry, conflict and helper regression passed.');
})().catch(e=>{console.error(e);process.exit(1)});
