// Start tests/upload_browser_fixture.py first; all requests stay on localhost.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.UPLOAD_FIXTURE_URL || 'http://127.0.0.1:8877';
(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 950}});
    await page.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/upload-process');
    await page.locator('.item-option').first().click();
    await page.locator('#videoTitle').fill('드래그 테스트');
    await page.locator('#nextStep').click();
    await page.locator('#srcText').fill('김치 100g과 밥 1공기를 식용유 1T에 3분간 볶는다.');
    await page.locator('#genBtn').click();
    await page.waitForFunction(() => RecipeEditor.valid());
    await page.locator('#confirmRecipe').click();
    const png = await (await page.request.get(base + '/_test/image')).body();
    await page.locator('#file').setInputFiles({name: 'test.png', mimeType: 'image/png', buffer: png});
    await page.waitForFunction(() => !document.getElementById('export').disabled);
    await page.locator('#title').fill('타이틀\n두 번째 줄');
    await page.locator('#subtitle').fill('서브타이틀');
    const canvas = page.locator('#canvas');
    await canvas.scrollIntoViewIfNeeded();
    const box = await canvas.boundingBox();
    const move = async (x, y, dx, dy) => {
      await page.mouse.move(box.x + box.width*x, box.y + box.height*y);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width*(x+dx), box.y + box.height*(y+dy), {steps: 5});
      await page.mouse.up();
    };
    await page.evaluate(() => {
      window.changes = 0;
      document.addEventListener('thumbnail:changed', () => window.changes++);
    });
    // Grab the second line and move diagonally: only vertical position changes.
    await move(.5, .55, .2, .1);
    assert.equal(await page.locator('#title-y').inputValue(), '60');
    assert.equal(await page.locator('#title-y-range').inputValue(), '60');
    assert.equal(await page.locator('#title-x').inputValue(), '50');
    assert.equal(await page.locator('#subtitle-y').inputValue(), '39');
    assert.ok(await page.evaluate(() => window.changes > 0));
    await move(.5, .39, -.2, -.1);
    assert.equal(await page.locator('#subtitle-y').inputValue(), '29');
    assert.equal(await page.locator('#subtitle-x').inputValue(), '50');
    // Horizontal-only movement does not change text or invalidate the image.
    const before = await page.evaluate(() => ({image: document.getElementById('canvas').toDataURL(), changes: window.changes}));
    await move(.5, .29, .2, 0);
    assert.deepEqual(await page.evaluate(() => ({image: document.getElementById('canvas').toDataURL(), changes: window.changes})), before);
    // Dragging outside the canvas stays captured and clamps to the control limits.
    await move(.5, .29, 0, -1);
    assert.equal(await page.locator('#subtitle-y').inputValue(), '5');
    await page.locator('#zoom').fill('200');
    await canvas.scrollIntoViewIfNeeded();
    const backgroundBefore = await canvas.evaluate(c => c.toDataURL());
    await move(.1, .8, 0, -.1);
    assert.notEqual(await canvas.evaluate(c => c.toDataURL()), backgroundBefore);
    assert.equal(await page.locator('#title-y').inputValue(), '60');
    assert.equal(await page.locator('#subtitle-y').inputValue(), '5');
    assert.deepEqual(errors, []);
    console.log('Vertical text drag, multiline hit detection, controls, bounds and background crop passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
