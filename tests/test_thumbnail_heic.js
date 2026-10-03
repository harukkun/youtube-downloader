// Start tests/upload_browser_fixture.py first; all requests stay on localhost.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8877';

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const context = await browser.newContext();
    await context.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    const errors = [], decoderRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.url().includes('/vendor/heic-to/')) decoderRequests.push(request.url()); });
    await page.goto(base + '/upload-process');
    const input = page.locator('#file');
    assert.match(await input.getAttribute('accept'), /image\/heic.*\.heic.*\.heif/);
    assert.equal(decoderRequests.length, 0);
    const heic = fs.readFileSync(path.join(__dirname, 'fixtures/thumbnail.heic'));
    const png = await (await page.request.get(base + '/_test/image')).body();
    // Real decoding, including uppercase extensions, absent MIME and MIME-only identification.
    for (const [name, mimeType] of [['photo.HEIC', ''], ['photo.heif', 'application/octet-stream'], ['photo', 'image/heic']]) {
      await input.setInputFiles({name, mimeType, buffer: heic});
      await page.waitForFunction(() => document.getElementById('videoStatus').textContent.includes('이미지를 선택했습니다'));
      assert.equal(await page.locator('#videoArea').isHidden(), true);
      assert.match(await page.locator('#frameInfo').innerText(), /108 × 192/);
      const pixel = await page.evaluate(() => Array.from(document.getElementById('canvas').getContext('2d').getImageData(540, 10, 1, 1).data));
      assert.ok(pixel[0] > 170 && pixel[0] < 210, 'Decoded gradient must appear on the canvas');
      await page.waitForFunction(() => !document.getElementById('export').disabled);
      const exported = await page.evaluate(async () => {
        const blob = await ThumbnailEditor.exportImage('jpg');
        const bitmap = await createImageBitmap(blob);
        const result = {type: blob.type, width: bitmap.width, height: bitmap.height, size: blob.size};
        bitmap.close(); return result;
      });
      assert.equal(exported.type, 'image/jpeg');
      assert.equal(exported.width, 1080); assert.equal(exported.height, 1920);
      assert.ok(exported.size > 0);
    }
    assert.equal(decoderRequests.length, 1);
    // Corrupt HEIC fails visibly and clears the previous frame; ordinary PNG still works.
    await input.setInputFiles({name: 'broken.heic', mimeType: 'image/heic', buffer: Buffer.from('invalid')});
    await page.waitForFunction(() => document.getElementById('videoStatus').classList.contains('error'));
    assert.equal(await page.locator('#export').isDisabled(), true);
    await input.setInputFiles({name: 'ordinary.png', mimeType: 'image/png', buffer: png});
    await page.waitForFunction(() => !document.getElementById('export').disabled);
    // A slow conversion must not overwrite a newer selection or reset editor.
    await page.route('**/vendor/heic-to/heic-to.js', route => route.fulfill({
      contentType: 'application/javascript',
      body: 'window.HeicTo = () => new Promise(resolve => { window.finishHeic = resolve; });'
    }));
    await page.reload();
    for (const reset of [false, true]) {
      await page.evaluate(() => { window.finishHeic = null; });
      await input.setInputFiles({name: 'slow.heic', mimeType: 'image/heic', buffer: heic});
      await page.waitForFunction(() => typeof window.finishHeic === 'function');
      if (reset) await page.evaluate(() => ThumbnailEditor.reset());
      else {
        await input.setInputFiles({name: 'newer.png', mimeType: 'image/png', buffer: png});
        await page.waitForFunction(() => !document.getElementById('export').disabled);
      }
      await page.evaluate(async () => {
        window.finishHeic(new Blob(['stale conversion']));
        await new Promise(resolve => setTimeout(resolve, 50));
      });
      assert.equal(await page.locator('#export').isDisabled(), reset);
      assert.equal(await page.locator('#videoStatus').evaluate(el => el.classList.contains('error')), false);
      if (!reset) assert.match(await page.locator('#fileInfo').innerText(), /newer.png/);
    }
    assert.deepEqual(errors, []);
    console.log('HEIC/HEIF decode, JPEG export, invalid files, PNG regression and stale conversion checks passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
