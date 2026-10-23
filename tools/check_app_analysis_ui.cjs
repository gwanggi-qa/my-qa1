// Explicit end-to-end check against the local server; never inputs to the tablet.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const apk = process.argv[2];
if (!apk || !fs.existsSync(apk)) throw Error('Supply the Appium Settings sample APK path.');
const output = path.join(root, 'qa_results/app-analysis-ui');
fs.mkdirSync(output, {recursive:true});
(async()=>{
  const browser = await chromium.launch({headless:true, channel:'msedge'});
  const errors=[];
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    page.on('pageerror', error=>errors.push(error.message));
    await page.goto('http://localhost:5000/app-analysis', {waitUntil:'networkidle'});
    await page.locator('#environment').getByText('REA: 6.4.0', {exact:false}).waitFor();
    await page.locator('#apk-file').setInputFiles(apk);
    await page.locator('#upload').click();
    await page.waitForFunction(()=>document.querySelector('#build-name').textContent.endsWith('.apk'));
    await page.locator('#analyze').click();
    await page.waitForFunction(()=>document.querySelector('#job-status').textContent==='정적 분석 완료', null, {timeout:180000});
    await page.getByText('io.appium.settings', {exact:true}).waitFor();
    assert.equal(await page.locator('#analysis-result').isVisible(), true);
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.locator('#permissions-tab').click();
    await page.locator('#search').fill('INTERNET');
    assert.equal(await page.locator('#table-body tr').count(), 1);
    assert.match(await page.locator('#table-body').innerText(), /android.permission.INTERNET/);
    await page.locator('#search').fill('');
    await page.locator('#components-tab').click();
    const count=await page.locator('#build-count').innerText();
    await page.locator('#upload').click();
    await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('이미 등록'));
    assert.equal(await page.locator('#build-count').innerText(), count);
    await page.locator('#analyze').click();
    await page.locator('#stop').click();
    await page.waitForFunction(()=>['중단됨','정적 분석 완료'].includes(document.querySelector('#job-status').textContent), null, {timeout:30000});
    const stopped=await page.locator('#job-status').innerText();
    assert.equal(stopped,'중단됨');
    await page.locator('#analyze').click();
    await page.waitForFunction(()=>document.querySelector('#job-status').textContent==='정적 분석 완료', null, {timeout:180000});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    const layout=await page.evaluate(()=>({
      viewport:innerWidth, width:document.documentElement.scrollWidth,
      badButtons:[...document.querySelectorAll('button')].filter(e=>e.clientWidth&&e.scrollWidth>e.clientWidth+1).map(e=>e.id)
    }));
    assert.ok(layout.width<=layout.viewport, JSON.stringify(layout));
    assert.deepEqual(layout.badButtons, []);
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#refresh').click();
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    const legacy = await context.newPage();
    await legacy.goto('http://localhost:5000/', {waitUntil:'domcontentloaded'});
    await legacy.waitForFunction(()=>document.querySelector('#screen')?.naturalWidth>0, null, {timeout:30000});
    const mirroring=await legacy.locator('#screen').evaluate(e=>({width:e.naturalWidth,height:e.naturalHeight}));
    await legacy.screenshot({path:path.join(output,'existing-workspace.png')});
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({uploaded:true,analyzed:true,duplicate_preserved:true,stop:stopped,layout,mirroring,errors}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
