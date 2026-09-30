// Run AUTO_EDIT_DATA_DIR=/tmp/autocut-ui-data HOOKS_DATA_DIR=/tmp/autocut-ui-hooks PORT=8879 .venv/bin/python app.py --no-browser
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050}}), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8879/auto-edit');
 await page.locator('#aeStart').click();
 assert.match(await page.locator('#aeError').innerText(),/영상을 넣어/);
 await page.locator('#aeFiles').setInputFiles('/tmp/autocut-fixture.mp4');
 await page.locator('#aeName').fill('브라우저 자동 편집 검증');
 await page.locator('#aeStart').click();
 await page.locator('#aeResult').waitFor({state:'visible',timeout:30000});
 assert.match(await page.locator('#aeSaved').innerText(),/1곳 제거/);
 await page.screenshot({path:'/tmp/auto-edit-desktop.png',fullPage:true});
 await page.reload();await page.locator('#aeResult').waitFor({state:'visible'});
 assert.equal(await page.locator('#aeResultName').innerText(),'브라우저 자동 편집 검증');
 const href=await page.locator('#aeDownload').getAttribute('href');
 const draft=await (await page.request.get('http://127.0.0.1:8879'+href)).json();
 assert.equal(draft.tracks[0].segments.length,2);
 // Re-import a real draft and run it through the same UI.
 await page.getByRole('button',{name:'캡컷 프로젝트',exact:true}).click();
 await page.locator('#aeJson').setInputFiles({name:'draft_content.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(draft))});
 await page.locator('#aeName').fill('프로젝트 재편집');
 await page.locator('#aeStart').click();
 await page.waitForFunction(()=>document.getElementById('aeResultName').textContent==='프로젝트 재편집'&&!document.getElementById('aeResult').hidden);
 // Keep automated tests from opening a real native app.
 await page.route('**/api/auto-edit/jobs/*/open',r=>r.fulfill({json:{ok:true,message:'테스트 열기 요청'}}));
 await page.locator('#aeOpen').click();
 await page.waitForFunction(()=>document.getElementById('aeOpenStatus').textContent==='테스트 열기 요청');
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/auto-edit-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 await browser.close(); console.log('auto edit browser: passed');
})().catch(e=>{console.error(e);process.exit(1)});
