// Run against upload_browser_fixture.py only; external requests are blocked.
'use strict';
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const base = process.env.UPLOAD_FIXTURE_URL || 'http://127.0.0.1:8877';

(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  const errors = [];
  try {
    const context = await browser.newContext({viewport:{width:1280,height:1000}});
    await context.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    await context.addInitScript(() => {
      window.copied = [];
      Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async text => {
        if (window.failCopy) throw new Error('Clipboard denied');
        window.copied.push(text);
      }}});
      document.execCommand = () => false;
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.setDefaultTimeout(10000);
    const tab = platform => page.locator('.platform-tab[data-platform="'+platform+'"]');
    const copied = () => page.evaluate(() => window.copied.at(-1));
    const generate = async () => {
      await page.locator('#genBtn').click();
      await page.waitForFunction(() => RecipeEditor.valid() && !document.getElementById('copyYoutubeTags').disabled);
    };
    await page.request.post(base+'/_test/control', {data:{reset:true}});
    await page.goto(base+'/upload-process');
    await page.locator('.item-option').nth(1).click();
    await page.locator('#videoTitle').fill('김치볶음밥 만들기');
    await page.locator('#nextStep').click();
    await page.locator('#srcText').fill('김치 100g 밥 1공기 식용유 1T를 3분간 볶음.');
    await generate();
    const generatedTags = '알쿡, 알쿡레시피, shorts, 김치볶음밥 만드는법, 김치볶음밥 레시피, 김치볶음밥 만드는방법, 김치볶음밥 맛있게, 테스트 요리 채널 레시피, 김치볶음밥, 볶음밥 만들기';
    assert.equal(await page.locator('#youtubeTags').inputValue(), generatedTags);
    await page.locator('#copyYoutubeTags').click();
    assert.equal(await copied(), generatedTags);
    await page.locator('#youtubeTags').fill('#집밥, 집밥,  간단한   한끼');
    await page.locator('#copyYoutubeTags').click();
    assert.equal(await copied(), '집밥, 간단한 한끼');
    await page.locator('#youtubeTags').fill('가'.repeat(494)+', 밥 볶음');
    assert.equal(await page.locator('#youtubeTagCount').innerText(), '501 / 500자');
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), true);
    await page.locator('#youtubeTags').fill('가'.repeat(493)+', 밥 볶음');
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), false);
    await page.locator('#youtubeTags').fill('집밥, 간단한 한끼');

    await tab('tiktok').click();
    assert.equal(await page.locator('#youtubeTagBox').isVisible(), false);
    assert.equal(await page.locator('#tiktokTags').inputValue(), '#알쿡 #김치볶음밥 #집밥 #알쿡레시피 #shorts #김치볶음밥만드는법 #김치볶음밥레시피 #김치볶음밥만드는방법 #김치볶음밥맛있게 #테스트요리채널레시피');
    await page.locator('#tiktokOut').fill('직접 수정한 레시피\n#알쿡 #한끼 #알쿡');
    await page.locator('#copyTiktokTags').click();
    assert.equal(await copied(), '#알쿡 #한끼');
    await page.locator('#copyBtn').click();
    assert.equal(await copied(), '직접 수정한 레시피\n#알쿡 #한끼 #알쿡');
    await page.evaluate(() => window.failCopy = true);
    await page.locator('#copyTiktokTags').click();
    assert.match(await page.locator('#recipeCopyStatus').innerText(), /자동 복사에 실패/);
    assert.equal(await page.locator('#tiktokTags').evaluate(el => el.value.slice(el.selectionStart, el.selectionEnd)), '#알쿡 #한끼');
    await page.evaluate(() => window.failCopy = false);
    await tab('instagram').click();
    assert.equal(await page.locator('#tiktokTagBox').isVisible(), false);
    await tab('youtube').click();
    await page.locator('#youtubeTagBox').evaluate(el => el.scrollIntoView({block:'center'}));
    await page.screenshot({path:'/tmp/recipe-tags-desktop.png'});

    // Persist the edited tags and use them in the final review without posting.
    await page.waitForFunction(() => document.getElementById('draftStatus').textContent.includes('저장했습니다'));
    await page.reload();
    await page.locator('#youtubeTags').waitFor({state:'visible'});
    assert.equal(await page.locator('#youtubeTags').inputValue(), '집밥, 간단한 한끼');
    await page.locator('#confirmRecipe').click();
    await page.locator('#useExisting').click();
    await page.locator('#nextStep').click();
    assert.equal(await page.locator('#reviewTags').innerText(), '집밥, 간단한 한끼');
    await page.getByRole('button', {name:'영상 제목 복사',exact:true}).click();
    assert.equal(await copied(), '김치볶음밥 만들기');
    await page.locator('#copyReviewDescription').click();
    assert.equal(await copied(), await page.locator('#reviewDescription').innerText());
    await page.locator('#copyReviewTags').click();
    assert.equal(await copied(), '집밥, 간단한 한끼');
    await page.evaluate(() => window.failCopy = true);
    await page.locator('#copyReviewTags').click();
    assert.match(await page.locator('#reviewCopyStatus').innerText(), /자동 복사에 실패/);
    assert.equal(await page.evaluate(() => window.getSelection().toString()), '집밥, 간단한 한끼');
    await page.evaluate(() => window.failCopy = false);

    // Legacy drafts remain valid, with an empty, manually editable tag field.
    await page.locator('[data-goto="2"]').click();
    await page.evaluate(async () => {
      const state = RecipeEditor.snapshot(); delete state.youtubeTags; delete state.tagsCurrent;
      await RecipeEditor.restore(state);
    });
    assert.equal(await page.evaluate(() => RecipeEditor.valid()), true);
    assert.equal(await page.locator('#youtubeTags').inputValue(), '');
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), true);
    await page.locator('#youtubeTags').fill('수동 태그');
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), false);

    // Mobile layout and post edits keep the hashtag copy list in sync.
    await page.setViewportSize({width:390,height:844});
    await tab('tiktok').click();
    await page.locator('#tiktokTags').scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('#tiktokTagBox').evaluate(el => el.scrollIntoView({block:'center'}));
    await page.screenshot({path:'/tmp/recipe-tags-mobile.png'});
    await page.locator('#tiktokOut').fill('해시태그 없는 게시글');
    assert.equal(await page.locator('#tiktokTags').inputValue(), '');
    assert.equal(await page.locator('#copyTiktokTags').isDisabled(), true);

    // Regeneration failure must not allow old tags to be copied as fresh results.
    await page.route('**/api/helper/recipe-description', route => route.fulfill({status:502,json:{error:'테스트 생성 실패'}}));
    await page.locator('#genBtn').click();
    await page.locator('#genErr').waitFor({state:'visible'});
    await tab('youtube').click();
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), true);
    assert.equal(await page.evaluate(() => RecipeEditor.valid()), false);
    await page.unroute('**/api/helper/recipe-description');
    await page.waitForFunction(() => document.getElementById('draftStatus').textContent.includes('저장했습니다'));
    await page.reload();
    await page.locator('#youtubeTags').waitFor({state:'visible'});
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), true);

    // A late result from another item must not install its tags in this one.
    let release;
    const delayed = new Promise(resolve => release = resolve);
    await page.route('**/api/helper/recipe-description', async route => {
      await delayed;
      await route.fulfill({json:{status:'complete',results:{instagram:'OLD',youtube:'OLD',tiktok:'#OLD'},tags:{youtube:['OLD'],tiktok:['#OLD']},notes:[]}});
    });
    await page.locator('#genBtn').click();
    await page.locator('[data-goto="0"]').click();
    await page.locator('.item-option').nth(0).click();
    release();
    await page.waitForTimeout(200);
    assert.equal(await page.locator('#youtubeTags').inputValue(), '');
    assert.equal(await page.locator('#tiktokTags').inputValue(), '');
    await page.unroute('**/api/helper/recipe-description');

    // Shared helper: required source channel, clarification, then tags.
    await page.request.post(base+'/_test/control', {data:{reset:true,mode:'questions'}});
    await page.goto(base+'/helper');
    await page.locator('#sourceChannel').fill('테스트 요리 채널');
    await page.locator('#srcText').fill('김치와 밥을 볶음');
    await page.locator('#genBtn').click();
    await page.locator('#answer-0').waitFor({state:'visible'});
    await tab('youtube').click();
    assert.equal(await page.locator('#youtubeTags').inputValue(), '');
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), true);
    await page.locator('#answer-0').fill('식용유 1T');
    await page.locator('#answerBtn').click();
    await page.waitForFunction(() => RecipeEditor.valid());
    await tab('youtube').click();
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), false);
    await page.locator('#clearOutBtn').click();
    assert.equal(await page.locator('#youtubeTags').inputValue(), '');
    assert.equal(await page.locator('#copyYoutubeTags').isDisabled(), true);
    assert.deepEqual(errors, []);
    await context.close();
    console.log('Recipe tags: copy, limits, drafts, review, mobile, failure and clarification passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
