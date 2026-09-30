// Start: ARTICLE_FIXTURE_DIR=/tmp/article-browser-test .venv/bin/python tests/article_browser_fixture.py
const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.ARTICLE_BASE||'http://127.0.0.1:8883';
 await page.goto(base+'/helper#article');await page.locator('#ab-items button').first().waitFor();
 assert.equal(await page.locator('#ab-panel').isVisible(),true);
 assert.equal(await page.locator('#ab-items button').count(),2);
 await page.locator('#ab-items button').first().click();await page.locator('#ab-source').waitFor({state:'visible'});
 await page.locator('#ab-source-title').fill('김치볶음밥');await page.locator('#ab-source-description').fill('밥 1공기, 기름 1T. 기름에 밥을 3분 볶는다.');await page.locator('#ab-source-url').fill('https://youtu.be/abcdefghijk');
 await page.locator('#ab-create').click();await page.locator('#ab-generate').waitFor({state:'visible'});
 let control=await(await page.request.get(base+'/_test/control')).json();assert.equal(control.posts,1);assert.equal(control.generations,0);
 await page.locator('#ab-generate').click();await page.locator('#ab-pending').waitFor({state:'visible'});await page.locator('#ab-apply').click();await page.locator('#ab-steps .ab-step').nth(1).waitFor();
 await page.locator('#ab-intro').fill('직접 다듬은 소개입니다.');await page.waitForTimeout(1000);await page.reload();await page.locator('#ab-intro').waitFor({state:'visible'});assert.equal(await page.locator('#ab-intro').inputValue(),'직접 다듬은 소개입니다.');
 // Cached generation does not call the model again or replace the edited article.
 await page.locator('#ab-generate').click();await page.locator('#ab-pending').waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#ab-work').disabled);
 assert.equal(await page.locator('#ab-intro').inputValue(),'직접 다듬은 소개입니다.');
 control=await(await page.request.get(base+'/_test/control')).json();assert.equal(control.generations,1);
 await page.locator('[data-ab-stage="2"]').click();await page.locator('#ab-prepare').click();await page.waitForFunction(()=>!document.getElementById('ab-transcribe').disabled&&!document.getElementById('ab-work').disabled);
 await page.locator('#ab-transcribe').click();await page.waitForFunction(()=>!document.getElementById('ab-suggest').disabled&&!document.getElementById('ab-work').disabled);
 await page.locator('#ab-suggest').click();await page.waitForFunction(()=>!document.getElementById('ab-work').disabled);await page.locator('[data-ab-stage="3"]').click();
 await page.waitForFunction(()=>document.getElementById('ab-video').readyState>=2);
 await page.locator('#ab-top').fill('25');await page.locator('#ab-bottom').fill('25');await page.locator('#ab-exclude').click();
 for(let i=0;i<2;i++){
   const options=await page.locator('#ab-step-select option').evaluateAll(os=>os.map(o=>o.value));await page.locator('#ab-step-select').selectOption(options[i]);
   await page.locator('#ab-suggestions button').first().click();await page.locator('#ab-candidates').click();await page.locator('#ab-candidate-images button').nth(4).waitFor();await page.waitForFunction(()=>!document.getElementById('ab-work').disabled);
   await page.locator('#ab-candidate-images button').nth(1).click();await page.waitForFunction(()=>!document.getElementById('ab-video').seeking);
   await page.locator('#ab-capture').click();await page.locator('#ab-still').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('ab-work').disabled&&!document.getElementById('ab-confirm').disabled);
   await page.locator('#ab-confirm').click();await page.waitForFunction(()=>document.getElementById('ab-image-status').textContent==='이미지 확정됨');
 }
 await page.screenshot({path:'/tmp/article-builder-desktop.png',fullPage:true});
 await page.locator('[data-ab-stage="4"]').click();await page.waitForFunction(()=>!document.getElementById('ab-export').disabled);
 await page.frameLocator('#ab-preview').locator('img').nth(1).waitFor();
 assert.equal(await page.frameLocator('#ab-preview').locator('img').count(),2);
 const download=page.waitForEvent('download');await page.locator('#ab-export').click();const output=await download;await output.saveAs('/tmp/article-builder-result.zip');
 control=await(await page.request.get(base+'/_test/control')).json();assert.equal(control.generations,2);
 await page.reload();await page.locator('#ab-preview').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('ab-export').disabled);
 // Mobile, explicit hash routing and existing helper still work.
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/article-builder-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await page.locator('.tool[data-tool="thumbnail"]').click();await page.locator('#file').waitFor({state:'attached'});assert.equal(await page.locator('[data-tool="thumbnail"].panel').isVisible(),true);
 await page.locator('.tool[data-tool="article"]').click();assert.equal(await page.locator('#ab-panel').isVisible(),true);
 // Draft version conflicts preserve the losing tab's input until explicit reload.
 await page.locator('[data-ab-stage="1"]').click();
 const other=await context.newPage();other.on('pageerror',e=>errors.push(e.message));await other.goto(base+'/helper#article');await other.locator('#ab-intro').waitFor({state:'visible'});
 await page.locator('#ab-intro').fill('첫 번째 탭에서 저장');await page.waitForTimeout(1000);
 await other.locator('#ab-intro').fill('두 번째 탭의 미저장 입력');await other.locator('#ab-conflict').waitFor({state:'visible'});
 assert.equal(await other.locator('#ab-intro').inputValue(),'두 번째 탭의 미저장 입력');
 await other.locator('#ab-reload').click();await other.waitForFunction(()=>document.getElementById('ab-intro').value==='첫 번째 탭에서 저장');await other.close();
 // Editing a step invalidates only its image approval; direct edits make no model calls.
 await page.locator('#ab-steps textarea').first().fill('기름을 팬에 골고루 두릅니다.');await page.waitForTimeout(1000);
 await page.locator('[data-ab-stage="4"]').click();assert.equal(await page.locator('#ab-export').isDisabled(),true);
 control=await(await page.request.get(base+'/_test/control')).json();assert.equal(control.generations,2);
 assert.deepEqual(errors,[]);await browser.close();console.log('Article browser: supplement, generation cache, autosave/reload, media, matching, crop, confirmation, ZIP, mobile and helper tabs passed.');
})().catch(e=>{console.error(e);process.exit(1)});
